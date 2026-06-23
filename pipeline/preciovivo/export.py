"""Export the store into a single JSON snapshot the Next.js dashboard reads.

Writes web/data/snapshot.json. This is the local-dev data bridge; in production
the dashboard reads the same shape from Supabase. Run:
    python -m preciovivo.export
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
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


def build(db_path: str = DB) -> dict:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    fechas = [r[0] for r in c.execute("SELECT DISTINCT fecha FROM precios_diarios ORDER BY fecha")]
    latest = fechas[-1] if fechas else None

    prods = {}
    q = """SELECT p.nombre_canonico AS nombre, p.categoria, pd.fecha, pd.unidad, pd.equiv_kg,
                  pd.precio_hoy_kg, pd.precio_ayer_kg, pd.precio_hoy_unit,
                  pd.masa_hoy, pd.masa_7d, pd.tendencia
           FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id
           ORDER BY p.nombre_canonico, pd.fecha"""
    for r in c.execute(q):
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

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mercado": "Gran Mercado Mayorista de Lima (GMML)",
        "latestFecha": latest,
        "fechas": fechas,
        "productCount": len(productos),
        "attribution": ATTRIBUTION,
        "productos": productos,
    }


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
