"""Ingest CLI: harvest -> parse -> load, idempotently.

  python -m preciovivo.ingest --latest 5         # last 5 business days
  python -m preciovivo.ingest --month 2026-06     # a whole month
  python -m preciovivo.ingest --backfill-months 6 # last N months
  python -m preciovivo.ingest --sample <pdf>      # parse+load a local PDF
  python -m preciovivo.ingest --forecast          # compute & store pronosticos
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
    ap.add_argument("--health", action="store_true", help="check source URLs are live, then exit")
    args = ap.parse_args(argv)

    if args.health:
        mp = H.latest_month_page()
        ds = H.dailies_in_month(mp) if mp else []
        print(f"health: month_page={'OK' if mp else 'FAIL'}  dailies_found={len(ds)}"
              + (f"  latest={ds[-1].fecha}" if ds else ""))
        return 0 if ds else 1

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
