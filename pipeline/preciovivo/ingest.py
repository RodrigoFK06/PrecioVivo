"""Ingest CLI: harvest -> parse -> load, idempotently.

  python -m preciovivo.ingest --latest 5         # last 5 business days
  python -m preciovivo.ingest --month 2026-06     # a whole month
  python -m preciovivo.ingest --backfill-months 6 # last N months
  python -m preciovivo.ingest --sample <pdf>      # parse+load a local PDF
  python -m preciovivo.ingest --forecast          # compute & store pronosticos
  python -m preciovivo.ingest --alerts            # evalúa e imprime alertas (no envía)
  python -m preciovivo.ingest --boletin [url|pdf] # boletín multi-mercado (MMF2/frutas)
  python -m preciovivo.ingest --sisap [pdf]       # SISAP: pollo vivo (AVES) + cross-check GMML
  python -m preciovivo.ingest --health            # source health check only

Store: Postgres/Supabase if DATABASE_URL is set, else local SQLite.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date

from . import harvester as H
from .parser import parse_report
from .store import Store

MIN_ROWS = 50  # write-block gate: a full GMML report has ~67-72 products


def _ingest_one(store: Store, daily: H.Daily) -> tuple[bool, str]:
    path, sha, nbytes = H.download(daily)
    store.record_raw(H.FUENTE, daily.fecha, daily.url, sha, nbytes)
    res = parse_report(path)
    # write-block gate (§4): never load a structurally broken report
    if res.fecha is None or len(res.rows) < MIN_ROWS:
        return False, (f"BLOCKED {daily.fecha}: fecha={res.fecha} rows={len(res.rows)} "
                       f"(<{MIN_ROWS}) — not loaded")
    if res.fecha != daily.fecha:
        return False, f"BLOCKED {daily.fecha}: PDF self-date {res.fecha} mismatches link date"
    n = store.upsert_precios(res.fecha, res.rows, H.FUENTE)
    warn = f"  ({len(res.warnings)} warn)" if res.warnings else ""
    return True, f"OK {daily.fecha}: {n} productos upserted{warn}"


def _targets(args) -> list[H.Daily]:
    if args.sample:
        return []
    if args.latest:
        return H.latest_dailies(args.latest)
    if args.month:
        y, m = map(int, args.month.split("-"))
        for mp in H.month_pages(max_sheets=4):
            ds = H.dailies_in_month(mp)
            if ds and ds[0].fecha.year == y and ds[0].fecha.month == m:
                return ds
        return []
    if args.backfill_months:
        out, seen = [], set()
        for mp in H.month_pages(max_sheets=4):
            for d in H.dailies_in_month(mp):
                if d.fecha not in seen:
                    seen.add(d.fecha)
                    out.append(d)
            if len({(d.fecha.year, d.fecha.month) for d in out}) >= args.backfill_months:
                break
        return sorted(out, key=lambda d: d.fecha)
    return H.latest_dailies(3)


def _db_path(store: Store) -> str:
    """Ruta SQLite del backend, o el default de export/forecast en Postgres."""
    if store.backend.startswith("sqlite:"):
        return store.backend.split("sqlite:", 1)[1]
    return os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")


def _run_alerts(store: Store) -> None:
    """Evalúa las reglas por defecto sobre el último día y las imprime.

    Detección honesta: NO envía nada (el emisor externo queda fuera). Reusa
    alerts.evaluate sobre el snapshot que arma export.build (que ya inyecta las
    anomalías), sin reabrir la BD por su cuenta.
    """
    from . import alerts as A
    from .export import build

    db = _db_path(store)
    snapshot = build(db)
    disparadas = A.evaluate(snapshot, A.DEFAULT_REGLAS)
    carga = A.carga_para_emisor(disparadas, canal="consola", snapshot=snapshot)

    print(f"alerts: {len(disparadas)} alerta(s) sobre {snapshot.get('latestFecha')} "
          f"· reglas={[r.nombre for r in A.DEFAULT_REGLAS]}")
    for a in disparadas:
        print(f"  [{a.tipo:<8}] {a.producto:<26.26} valor={a.valor:+8.2f} {a.fecha}")
        print(f"             {a.mensaje}")
    if not disparadas:
        print("  (ninguna con las reglas vigentes)")
    print(f"carga emisor (no se envía): asunto={carga['asunto']!r} "
          f"n_alertas={carga['n_alertas']}")


def _boletin_pdf_path(arg: str):
    """Resuelve el argumento de --boletin a una ruta de PDF local.

    arg puede ser: '' (vacío -> PDF cacheado o SAMPLE_URL), una ruta a un .pdf
    existente, o una URL CDN. Devuelve (path, descargado:bool) o (None, False).
    """
    from . import boletin as B

    if arg and os.path.exists(arg):
        return arg, False
    if arg.lower().startswith("http"):
        path, _sha, _n = B.fetch_boletin(arg)
        return path, True
    # sin valor: usa el último PDF cacheado; si no hay, descarga el de muestra.
    cached = sorted(glob.glob(os.path.join(B.RAW_DIR, f"{B.FUENTE}_*.pdf")))
    if cached:
        return cached[-1], False
    path, _sha, _n = B.fetch_boletin(B.SAMPLE_URL)
    return path, True


def _run_boletin(store: Store, arg: str) -> None:
    """Descarga+parsea el boletín multi-mercado y hace upsert por mercado.

    NO BLOQUEANTE: si el boletín falla (sin red, formato raro), reporta y sigue
    sin tocar los datos GMML diarios. El upsert usa el mercado_id correspondiente
    (MMF2/frutas y también el GMML del boletín) — la tabla `mercados` ya existe.
    """
    from . import boletin as B
    from .parser import ProductRow

    try:
        path, descargado = _boletin_pdf_path(arg)
    except Exception as e:  # noqa: BLE001 - no debe romper el pipeline GMML
        print(f"boletin: SKIP (no se pudo obtener el PDF: {type(e).__name__}: {e})")
        return
    if path is None:
        print("boletin: SKIP (sin PDF cacheado y sin URL/red)")
        return

    rows = B.parse_boletin(path)
    cov = B.coverage(path)
    fecha = rows[0]["fecha"] if rows else None
    print(f"boletin: {os.path.basename(path)} "
          f"{'(descargado)' if descargado else '(cacheado)'} · fecha={fecha} · "
          f"{len(rows)} filas · cobertura {cov['cobertura_pct']}% {cov['por_mercado']}")
    if not rows or fecha is None:
        print("boletin: SKIP upsert (parser parcial / sin fecha) — GMML intacto")
        return

    # Provenance del crudo (sha del archivo en disco).
    try:
        import hashlib
        with open(path, "rb") as f:
            data = f.read()
        store.record_raw(B.FUENTE, fecha, path, hashlib.sha256(data).hexdigest(), len(data))
    except Exception as e:  # noqa: BLE001 - provenance opcional, no bloquea
        print(f"  (aviso: no se registró el crudo: {type(e).__name__}: {e})")

    # Agrupa por codigo de mercado y construye ProductRow (precio ya S/ por kg).
    # IMPORTANTE: se OMITE el mercado GMML del boletín. La serie GMML diaria viene
    # del reporte-335 (precio del día); la columna GMML del boletín es un PROMEDIO
    # semanal de otra semántica. Mezclarlos contaminaría la serie diaria y los
    # pronósticos. El valor multi-mercado del boletín es el mercado de FRUTAS
    # (MMF2) y demás, que el reporte-335 no cubre.
    por_merc: dict[str, list[ProductRow]] = {}
    omitidos_gmml = 0
    for r in rows:
        cod = r["mercado"]
        if cod == "GMML":
            omitidos_gmml += 1
            continue
        # precio_kg ya viene en S/ por kg -> equiv_kg=1.0 hace precio_hoy_kg=precio_kg.
        pr = ProductRow(
            producto=r["producto"],
            masa_ayer=None, masa_hoy=None, masa_7d=None, masa_4lun=None,
            unidad="Kilogramo", equiv_kg=1.0,
            precio_ayer_unit=None, precio_hoy_unit=r["precio_kg"], precio_7d_unit=None,
            tendencia=None,
        )
        por_merc.setdefault(cod, []).append(pr)

    total = 0
    for cod, prs in sorted(por_merc.items()):
        mid = store.mercado_para_codigo(cod)  # crea el mercado bajo demanda
        n = store.upsert_precios(fecha, prs, B.FUENTE, mercado_id=mid)
        total += n
        print(f"  {cod} (mercado_id={mid}): {n} productos upserted")
    print(f"boletin: {total} filas upserted en {len(por_merc)} mercado(s) "
          f"(GMML del boletín omitido: {omitidos_gmml} filas; la serie GMML diaria "
          f"del reporte-335 queda intacta)")


def _sisap_aves_rows(rows: list[dict]):
    """Filas AVES VIVAS del parse SISAP -> (fecha, [ProductRow]) para upsert.

    Solo el mercado de aves se ingesta como serie propia: es data que el
    reporte-335 NO tiene (pollo vivo, el producto de mayor reconocimiento).
    El precio ya viene en S/ por kg -> equiv_kg=1.0. El nombre se pasa a Title
    Case para alinear con los nombres canónicos del diccionario de productos.
    """
    from .parser import ProductRow
    from .sisap import AVES_LABEL

    fecha, prs = None, []
    for r in rows:
        if r["mercado"] != AVES_LABEL or r["precio_hoy_kg"] is None:
            continue
        fecha = fecha or r["fecha"]
        prs.append(ProductRow(
            producto=r["producto"].title(),
            masa_ayer=None, masa_hoy=None, masa_7d=None, masa_4lun=None,
            unidad="Kilogramo", equiv_kg=1.0,
            precio_ayer_unit=r["precio_ayer_kg"], precio_hoy_unit=r["precio_hoy_kg"],
            precio_7d_unit=r["prom_7d"],
            tendencia=None,
        ))
    return fecha, prs


def _run_sisap(store: Store, arg: str) -> None:
    """Descarga+parsea el reporte SISAP: ingesta AVES y contrasta GMML (§4).

    NO BLOQUEANTE: cualquier fallo reporta y sigue sin tocar los datos GMML.
    Dos salidas:
      1) upsert de AVES VIVAS (pollo vivo) como mercado 'AVES', fuente 'sisap';
      2) cross-check de los precios GMML de SISAP contra nuestra BD, impreso y
         persistido en sisap_check.json para que export lo adjunte al snapshot.
    Los precios GMML y MMF2 de SISAP NO se upsertan: GMML ya viene del
    reporte-335 (SISAP es su segunda opinión, mezclarlos anularía la redundancia)
    y MMF2 llega vía el boletín con otra semántica.
    """
    from . import sisap as S

    # PDF: ruta local si se pasó; si no, intenta descargar; si no hay red, el
    # último cacheado.
    path = None
    try:
        if arg and os.path.exists(arg):
            path, descargado = arg, False
        else:
            path, _sha, _n = S.fetch_sisap()
            descargado = True
    except Exception as e:  # noqa: BLE001 - no debe romper el pipeline GMML
        cached = sorted(glob.glob(os.path.join(S.RAW_DIR, f"{S.FUENTE}_*.pdf")))
        if not cached:
            print(f"sisap: SKIP (sin PDF: {type(e).__name__}: {e})")
            return
        path, descargado = cached[-1], False
        print(f"sisap: sin red ({type(e).__name__}), usando cacheado {os.path.basename(path)}")

    try:
        rows = S.parse_sisap(path)
    except Exception as e:  # noqa: BLE001
        print(f"sisap: SKIP (parser: {type(e).__name__}: {e}) — GMML intacto")
        return
    fecha = rows[0]["fecha"] if rows else None
    print(f"sisap: {os.path.basename(path)} "
          f"{'(descargado)' if descargado else '(local)'} · fecha={fecha} · {len(rows)} filas")
    if not rows or fecha is None:
        print("sisap: SKIP (sin filas o sin fecha) — GMML intacto")
        return

    # Provenance del crudo (sha del archivo en disco), como en --boletin.
    try:
        import hashlib
        with open(path, "rb") as f:
            data = f.read()
        store.record_raw(S.FUENTE, fecha, S.SISAP_URL, hashlib.sha256(data).hexdigest(), len(data))
    except Exception as e:  # noqa: BLE001 - provenance opcional, no bloquea
        print(f"  (aviso: no se registró el crudo: {type(e).__name__}: {e})")

    # 1) AVES VIVAS -> serie propia (pollo vivo).
    fecha_aves, prs = _sisap_aves_rows(rows)
    if prs:
        mid = store.mercado_para_codigo("AVES")
        n = store.upsert_precios(fecha_aves, prs, S.FUENTE, mercado_id=mid)
        print(f"  AVES (mercado_id={mid}): {n} producto(s) upserted "
              f"({', '.join(p.producto for p in prs)})")
    else:
        print("  AVES: sin filas con precio — nada que upsertar")

    # 2) Cross-check GMML (§4): SISAP como segunda opinión, persistido para export.
    cc = S.cross_check(rows, _db_path(store))
    check = S.save_check(cc, _db_path(store))
    if not check["gmml_disponible"]:
        # SISAP publica ~06:30; el reporte-335 del mismo día suele llegar después.
        # Sin ningún precio GMML de esa fecha el contraste es prematuro, no una
        # divergencia: no vale la pena listar producto por producto.
        print(f"  cross-check GMML: prematuro — sin reporte-335 del {check['fecha']} "
              f"en la BD todavía ({check['contrastados']} productos quedan en espera)")
        return
    flags = [r for r in check["resultados"] if r["flag"]]
    print(f"  cross-check GMML: {check['coinciden']}/{check['contrastados']} coinciden "
          f"(umbral ±{check['umbral_pct']}%) -> sisap_check.json")
    for r in flags:
        print(f"    [!] {r['producto']:<24.24} sisap={r['sisap_kg']!s:>6} "
              f"gmml={r['gmml_kg']!s:>6}  {r['detalle']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Precio Vivo ingest")
    ap.add_argument("--latest", type=int, metavar="N")
    ap.add_argument("--month", metavar="YYYY-MM")
    ap.add_argument("--backfill-months", type=int, metavar="N")
    ap.add_argument("--sample", metavar="PDF")
    ap.add_argument("--reparse", action="store_true",
                    help="re-parse cached raw PDFs in data/raw (no download) and re-load")
    ap.add_argument("--forecast", action="store_true",
                    help="compute honest forecasts for all products and store them")
    ap.add_argument("--alerts", action="store_true",
                    help="evalúa las reglas de alerta sobre el último día y las imprime "
                         "(no envía nada; detección honesta sobre datos ya calculados)")
    ap.add_argument("--boletin", nargs="?", const="", metavar="URL_O_PDF",
                    help="ingesta el boletín multi-mercado (GMML+MMF2/frutas). Acepta "
                         "una URL CDN o una ruta a PDF; sin valor usa el PDF cacheado "
                         "o el SAMPLE_URL. NO bloquea ni altera la ingesta GMML diaria")
    ap.add_argument("--sisap", nargs="?", const="", metavar="PDF",
                    help="ingesta el reporte SISAP-MIDAGRI: AVES VIVAS (pollo vivo) como "
                         "serie propia + cross-check de GMML contra la BD (§4). Acepta una "
                         "ruta a PDF local; sin valor descarga el del día (o usa el "
                         "cacheado sin red). NO bloquea la ingesta GMML diaria")
    ap.add_argument("--health", action="store_true", help="check source URLs are live, then exit")
    ap.add_argument("--index", action="store_true",
                    help="construye el índice RAG desde el snapshot y publica el "
                         "artefacto estático que lee el sitio. Requiere el snapshot "
                         "ya exportado (python -m preciovivo.export)")
    ap.add_argument("--index-granularidad", default="semana",
                    choices=["dia", "semana", "mes"],
                    help="grano de los chunks producto-periodo (default: semana; "
                         "ver evals/run_retrieval.py --granularidad todas)")
    ap.add_argument("--index-solo-reciente", action="store_true",
                    help="no reescribe la parte histórica del artefacto. Es el modo "
                         "de la corrida DIARIA: el corpus pasado es inmutable y "
                         "reescribirlo engordaría el repo ~2 MB por día")
    ap.add_argument("--no-publicar", action="store_true",
                    help="construye el índice pero NO escribe el artefacto del sitio")
    ap.add_argument("--permitir-embedder-local", action="store_true",
                    help="publica el artefacto aunque se haya construido con el "
                         "embebedor local (model2vec). Por defecto está BLOQUEADO: "
                         "el sitio embebe la consulta por HTTP y no puede correr un "
                         "modelo local, así que la firma no coincidiría y la "
                         "recuperación vectorial quedaría apagada en silencio. "
                         "Úsalo solo para ensayos a un directorio temporal")
    args = ap.parse_args(argv)

    if args.health:
        mp = H.latest_month_page()
        ds = H.dailies_in_month(mp) if mp else []
        print(f"health: month_page={'OK' if mp else 'FAIL'}  dailies_found={len(ds)}"
              + (f"  latest={ds[-1].fecha}" if ds else ""))
        return 0 if ds else 1

    if args.index:
        # No toca la BD: el corpus se construye desde el snapshot ya exportado
        # (ver preciovivo.corpus, sección FUENTE). Por eso va antes de abrir Store.
        from .indexer import main_index
        snapshot = os.environ.get("PRECIOVIVO_EXPORT", "../web/data/snapshot.json")
        if not os.path.exists(snapshot):
            print(f"No encuentro el snapshot en {snapshot}. "
                  f"Corre primero: python -m preciovivo.export")
            return 1
        return main_index(snapshot, granularidad=args.index_granularidad,
                          solo_reciente=args.index_solo_reciente,
                          publicar=not args.no_publicar,
                          permitir_local=args.permitir_embedder_local)

    store = Store()
    store.init_schema()
    print(f"store: {store.backend}")

    if args.sample:
        res = parse_report(args.sample)
        if res.fecha is None or len(res.rows) < MIN_ROWS:
            print(f"BLOCKED sample: fecha={res.fecha} rows={len(res.rows)}")
            return 1
        n = store.upsert_precios(res.fecha, res.rows, H.FUENTE)
        print(f"OK sample {res.fecha}: {n} productos upserted ({len(res.warnings)} warn)")
    elif args.reparse:
        files = sorted(glob.glob(os.path.join(H.RAW_DIR, f"{H.FUENTE}_*.pdf")))
        print(f"reparse: {len(files)} cached PDFs in {H.RAW_DIR}")
        ok = blocked = warn = 0
        for path in files:
            res = parse_report(path)
            if res.fecha is None or len(res.rows) < MIN_ROWS:
                print(f"  BLOCKED {os.path.basename(path)}: fecha={res.fecha} rows={len(res.rows)}")
                blocked += 1
                continue
            store.upsert_precios(res.fecha, res.rows, H.FUENTE)
            ok += 1
            warn += len(res.warnings)
        print(f"summary: {ok} reparsed, {blocked} blocked, {warn} total warnings")
    elif args.forecast:
        from . import forecast as F
        # forecast.py lee SQLite por db_path (o Postgres si DATABASE_URL). Pasamos
        # la ruta del backend SQLite local; en Postgres usa DATABASE_URL internamente.
        db_path = store.backend.split("sqlite:", 1)[1] if store.backend.startswith("sqlite:") \
            else os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
        res = F.forecast_all(db_path)
        F.save_cache(res, db_path)  # export lo reutiliza sin recomputar el GBM
        por = res.get("por_slug", {})
        kg = res.get("kill_gate", {})
        items = list(por.items())
        hoy = date.today()
        n = store.upsert_pronosticos(hoy, items)
        con_pron = sum(1 for _, fc in items if fc.get("metodo") not in ("sin_datos",))
        print(f"forecast: {n} pronósticos guardados (fecha_generado={hoy.isoformat()}) · "
              f"{con_pron} con modelo, {len(items) - con_pron} sin datos")
        print(f"kill-gate: volume_helps={kg.get('volume_helps')} "
              f"mae_baseline={kg.get('mae_baseline')} "
              f"mae_con_volumen={kg.get('mae_con_volumen')} "
              f"mejora_pct={kg.get('mejora_pct')} n_productos={kg.get('n_productos')}")
    elif args.alerts:
        _run_alerts(store)
    elif args.boletin is not None:
        _run_boletin(store, args.boletin)
    elif args.sisap is not None:
        _run_sisap(store, args.sisap)
    else:
        targets = _targets(args)
        print(f"targets: {len(targets)} dailies"
              + (f" ({targets[0].fecha}..{targets[-1].fecha})" if targets else ""))
        ok = blocked = 0
        for d in targets:
            try:
                good, msg = _ingest_one(store, d)
            except Exception as e:  # noqa: BLE001 - report and continue
                good, msg = False, f"ERROR {d.fecha}: {type(e).__name__}: {e}"
            print(" ", msg)
            ok += good
            blocked += not good
        print(f"summary: {ok} loaded, {blocked} blocked/error")

    fechas = store.distinct_fechas()
    print(f"db now: {store.count('precios_diarios')} filas precio · "
          f"{store.count('productos')} productos · {len(fechas)} fechas"
          + (f" ({fechas[0]}..{fechas[-1]})" if fechas else ""))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
