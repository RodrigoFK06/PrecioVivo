"""Export the store into a single JSON snapshot the Next.js dashboard reads.

Writes web/data/snapshot.json. This is the local-dev data bridge; in production
the dashboard reads the same shape from Supabase. Run:
    python -m preciovivo.export
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone

DB = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
OUT = os.environ.get("PRECIOVIVO_EXPORT", "../web/data/snapshot.json")

ATTRIBUTION = "Fuente: MIDAGRI – GMML, procesado por Precio Vivo · cifras referenciales, no oficiales."


def slugify(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s


def _round(x, n=4):
    return round(x, n) if isinstance(x, (int, float)) else None


def _gmml_id(lec) -> int | None:
    """id del mercado GMML, o None si la tabla aún no lo tiene."""
    return lec.valor("SELECT id FROM mercados WHERE codigo = 'GMML'")


def build(db_path: str = DB) -> dict:
    # Lectura dual-backend: SQLite en dev, Postgres si hay DATABASE_URL. Antes
    # esto era `sqlite3.connect` directo, así que con DATABASE_URL se podía
    # ingerir pero no exportar — y el snapshot es la fuente de todo lo demás.
    from .store import LectorDatos

    c = LectorDatos(db_path)
    # El snapshot base es GMML (reporte-335 diario). Con multi-mercado en la BD
    # (boletín MMF2/frutas), filtrar por el mercado GMML mantiene la forma y los
    # números EXACTOS del contrato del dashboard; los demás mercados se exponen
    # aparte en snapshot.mercados (retro-compatible: el dashboard puede ignorarlo).
    gmml_id = _gmml_id(c)
    where_g = f"WHERE pd.mercado_id = {c.ph}" if gmml_id is not None else ""
    args_g = (gmml_id,) if gmml_id is not None else ()
    fechas = [r["fecha"] for r in c.filas(
        f"SELECT DISTINCT fecha FROM precios_diarios pd {where_g} ORDER BY fecha", args_g)]
    latest = fechas[-1] if fechas else None

    prods = {}
    q = f"""SELECT p.nombre_canonico AS nombre, p.categoria, pd.fecha, pd.unidad, pd.equiv_kg,
                  pd.precio_hoy_kg, pd.precio_ayer_kg, pd.precio_hoy_unit,
                  pd.masa_hoy, pd.masa_7d, pd.tendencia
           FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id
           {where_g}
           ORDER BY p.nombre_canonico, pd.fecha"""
    for r in c.filas(q, args_g):
        p = prods.setdefault(r["nombre"], {
            "nombre": r["nombre"], "slug": slugify(r["nombre"]),
            "categoria": r["categoria"], "unidad": r["unidad"],
            "equiv_kg": _round(r["equiv_kg"]), "series": [],
        })
        p["unidad"] = r["unidad"]
        p["series"].append({
            "fecha": r["fecha"],
            "precio_kg": _round(r["precio_hoy_kg"], 4),
            "masa_hoy": _round(r["masa_hoy"], 1),
            "masa_7d": _round(r["masa_7d"], 1),
            "tendencia": r["tendencia"],
            "_ayer_kg": _round(r["precio_ayer_kg"], 4),
        })

    productos = []
    for p in prods.values():
        s = p["series"]
        last = s[-1]
        # Excluir fantasmas/stale: solo productos presentes en el último reporte.
        # (un producto cuyo último dato no es la fecha más reciente no tiene
        # precio "de hoy" y arrastraría cifras viejas como si fueran actuales.)
        if latest is not None and last["fecha"] != latest:
            continue
        ayer = last["_ayer_kg"]
        hoy = last["precio_kg"]
        var = None
        if ayer and hoy is not None and ayer != 0:
            var = round(100.0 * (hoy - ayer) / ayer, 1)
        for pt in s:
            pt.pop("_ayer_kg", None)
        productos.append({
            **p,
            "latest": {
                "fecha": last["fecha"], "precio_kg": hoy, "precio_ayer_kg": ayer,
                "var_pct": var, "masa_hoy": last["masa_hoy"], "masa_7d": last["masa_7d"],
                "tendencia": last["tendencia"],
            },
        })
    productos.sort(key=lambda x: x["nombre"])

    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mercado": "Gran Mercado Mayorista de Lima (GMML)",
        "latestFecha": latest,
        "fechas": fechas,
        "productCount": len(productos),
        "attribution": ATTRIBUTION,
        "productos": productos,
    }

    # --- Fase 3: pronósticos honestos + kill-gate volumen->precio --------------
    _add_forecast(snapshot, productos, db_path)
    # --- Fase 4: capa IA (anomalías estadísticas + resumen del día) -----------
    _add_ia(snapshot, productos, db_path)
    # --- Multi-mercado (boletín MMF2/frutas, SISAP/aves): retro-compatible -----
    _add_mercados(snapshot, c, gmml_id)
    # --- Fase 4: alertas accionables (detección honesta; no envía nada) -------
    _add_alertas(snapshot)
    # --- Fase 2 §4: verificación cruzada SISAP-MIDAGRI (aditiva, opcional) ----
    _add_verificacion(snapshot, db_path)

    c.close()
    return snapshot


def _add_forecast(snapshot: dict, productos: list[dict], db_path: str) -> None:
    """Adjunta producto.forecast (por producto) y snapshot.forecastMeta.kill_gate.

    Lee la BD una sola vez vía forecast.forecast_all y mapea por nombre_canonico,
    que es la clave de `por_slug` y el 'nombre' de cada producto del snapshot.
    Si forecast falla, no rompe el snapshot base (degrada con forecastMeta vacío).
    """
    try:
        from . import forecast as F
        res = F.forecast_all_cached(db_path)  # lee el cache de `ingest --forecast`; no recomputa el GBM
        por = res.get("por_slug", {})
        for p in productos:
            fc = por.get(p["nombre"])
            if fc is not None:
                p["forecast"] = fc
        snapshot["forecastMeta"] = {"kill_gate": res.get("kill_gate", {})}
    except Exception as e:  # noqa: BLE001 - el snapshot base no debe caerse
        snapshot["forecastMeta"] = {"kill_gate": {}, "error": f"{type(e).__name__}: {e}"}


def _build_facts(snapshot: dict, productos: list[dict]) -> dict:
    """Arma 'facts' para ai.daily_summary desde el snapshot ya construido.

    No re-lee la BD: usa los latest/var_pct de cada producto. subas/bajas son los
    mayores movimientos del último día; ingreso_total_t es la suma de masa_hoy.
    """
    movers = []
    ingreso = 0.0
    tiene_ingreso = False
    for p in productos:
        lat = p.get("latest") or {}
        var = lat.get("var_pct")
        if var is not None:
            pk = lat.get("precio_kg")
            movers.append({"nombre": p["nombre"], "var_pct": var,
                           "precio_kg": round(pk, 2) if pk is not None else None})
        m = lat.get("masa_hoy")
        if m is not None:
            ingreso += float(m)
            tiene_ingreso = True
    movers.sort(key=lambda m: m["var_pct"])
    subas = [m for m in reversed(movers[-3:]) if m["var_pct"] > 0] if movers else []
    bajas = [m for m in movers[:3] if m["var_pct"] < 0] if movers else []
    return {
        "fecha": snapshot.get("latestFecha"),
        "mercado": snapshot.get("mercado"),
        "n_productos": len(productos),
        "subas": subas,
        "bajas": bajas,
        "ingreso_total_t": round(ingreso, 1) if tiene_ingreso else None,
    }


def _add_ia(snapshot: dict, productos: list[dict], db_path: str) -> None:
    """Adjunta snapshot.anomalias (z-score robusto) y snapshot.resumenIA.

    detecta_anomalias recibe el snapshot ya construido (sin reabrir la BD).
    daily_summary degrada a fuente='fallback' sin AI_API_KEY. Si la capa
    falla, el snapshot base se conserva con anomalias=[] y un resumen vacío.
    """
    try:
        from .ai import daily_summary, detecta_anomalias
        snapshot["anomalias"] = detecta_anomalias(snapshot)
        facts = _build_facts(snapshot, productos)
        snapshot["resumenIA"] = daily_summary(facts)
    except Exception as e:  # noqa: BLE001 - el snapshot base no debe caerse
        snapshot.setdefault("anomalias", [])
        snapshot.setdefault("resumenIA", {"texto": "", "fuente": "fallback"})
        snapshot["iaError"] = f"{type(e).__name__}: {e}"


def _add_mercados(snapshot: dict, c, gmml_id) -> None:
    """Adjunta snapshot.mercados con los mercados NO-GMML presentes (p. ej. MMF2).

    RETRO-COMPATIBLE: el bloque `productos` (GMML) no cambia. Esta sección es
    aditiva: si no hay datos de otros mercados, queda como lista vacía y el
    dashboard puede ignorarla. Para cada mercado expone su último día y los
    productos de ese día (nombre, slug, precio_kg, var_pct). Si la tabla
    `mercados` no existe o algo falla, degrada a [] sin romper el snapshot.
    """
    try:
        rows = c.filas(
            "SELECT m.codigo, m.nombre, p.nombre_canonico, pd.fecha, "
            "       pd.precio_hoy_kg, pd.precio_ayer_kg "
            "FROM precios_diarios pd "
            "JOIN productos p ON p.id = pd.producto_id "
            "JOIN mercados m ON m.id = pd.mercado_id "
            + (f"WHERE pd.mercado_id <> {c.ph} " if gmml_id is not None else "")
            + "ORDER BY m.codigo, pd.fecha, p.nombre_canonico",
            (gmml_id,) if gmml_id is not None else (),
        )
    except Exception as e:  # noqa: BLE001 - tabla ausente u otra rareza -> aditivo vacío
        # Amplio a propósito: cada driver lanza su propia jerarquía de errores
        # (sqlite3.Error vs psycopg.Error) y esta sección es ADITIVA — que falte
        # no debe tumbar el snapshot base.
        c.rollback()  # en Postgres, sin esto la conexión queda inutilizable
        snapshot["mercados"] = []
        snapshot["mercadosError"] = f"{type(e).__name__}: {e}"
        return

    por_cod: dict[str, dict] = {}
    ultima_fecha: dict[str, str] = {}
    for r in rows:
        cod = r["codigo"]
        ultima_fecha[cod] = r["fecha"]  # filas ya ordenadas por fecha asc
    for r in rows:
        cod = r["codigo"]
        if r["fecha"] != ultima_fecha.get(cod):
            continue  # solo el último día disponible de cada mercado
        m = por_cod.setdefault(cod, {
            "codigo": cod, "nombre": r["nombre"],
            "latestFecha": r["fecha"], "productos": [],
        })
        hoy = _round(r["precio_hoy_kg"], 4)
        ayer = _round(r["precio_ayer_kg"], 4)
        var = None
        if ayer and hoy is not None and ayer != 0:
            var = round(100.0 * (hoy - ayer) / ayer, 1)
        m["productos"].append({
            "nombre": r["nombre_canonico"],
            "slug": slugify(r["nombre_canonico"]),
            "precio_kg": hoy,
            "var_pct": var,
        })
    snapshot["mercados"] = [por_cod[k] for k in sorted(por_cod)]


def _add_alertas(snapshot: dict) -> None:
    """Adjunta snapshot.alertas: lista de alertas accionables ya disparadas.

    Reusa alerts.evaluate sobre el snapshot YA construido (que incluye latest,
    var_pct y anomalías), sin reabrir la BD. Cada alerta es un dict del contrato
    {producto, slug, tipo, mensaje, valor, fecha, regla}, listo para el dashboard.
    Detección honesta: NO envía nada. Si la capa falla, alertas=[] (no rompe).
    """
    try:
        from .alerts import DEFAULT_REGLAS, evaluate
        disparadas = evaluate(snapshot, DEFAULT_REGLAS)
        snapshot["alertas"] = [a.to_dict() for a in disparadas]
    except Exception as e:  # noqa: BLE001 - el snapshot base no debe caerse
        snapshot.setdefault("alertas", [])
        snapshot["alertasError"] = f"{type(e).__name__}: {e}"


def _add_verificacion(snapshot: dict, db_path: str) -> None:
    """Adjunta snapshot.verificacion: el cross-check GMML vs SISAP-MIDAGRI (§4).

    Lee el cache sisap_check.json que escribe `ingest --sisap` (sin red ni
    re-parseo). ADITIVO: si no hay cache, el bloque simplemente no aparece y el
    dashboard lo ignora. `vigente` marca si el contraste corresponde al último
    día del snapshot (un check viejo se muestra como histórico, no como de hoy).
    """
    try:
        from .sisap import load_check
        check = load_check(db_path)
        if not check:
            return
        check["vigente"] = (check.get("fecha") is not None
                            and check.get("fecha") == snapshot.get("latestFecha"))
        snapshot["verificacion"] = check
    except Exception as e:  # noqa: BLE001 - el snapshot base no debe caerse
        snapshot["verificacionError"] = f"{type(e).__name__}: {e}"


def main():
    data = build()
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT)
    print(f"wrote {OUT}  ({size/1024:.0f} KB)  "
          f"{data['productCount']} productos · {len(data['fechas'])} fechas · latest {data['latestFecha']}")


if __name__ == "__main__":
    main()
