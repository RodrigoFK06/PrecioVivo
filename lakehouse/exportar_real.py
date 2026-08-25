"""Exporta la serie real del GMML a CSV, para subirla a Databricks.

    python lakehouse/exportar_real.py

Son ~38.500 filas, del orden de 2 MB. Eso sube por la interfaz sin drama, a
diferencia de los gigabytes de la prueba de carga, que se generan alla dentro.

POR QUE UN CSV Y NO PARQUET
----------------------------
Parquet seria mas pequeno y traeria los tipos puestos. Se elige CSV igualmente
por una razon concreta: **la carga desde CSV obliga a declarar el esquema**, y
declararlo es justo la parte que hay que aprender de la capa bronze. Con Parquet
el esquema viene de regalo y el paso se vuelve invisible.

Es una decision pedagogica y se dice, en vez de presentarla como tecnica.

QUE LLEVA Y QUE NO
-------------------
Lleva: fecha, producto, mercado, precio por kilo, masa, unidad, equivalencia.
NO lleva: nada que no este ya publicado en `web/data/snapshot.json`, que es un
artefacto publico del repositorio. No hay dato personal ni de cliente.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(AQUI, "..", "data", "preciovivo.db")
SALIDA = os.path.join(AQUI, "gmml_real.csv")

CONSULTA = """
select p.fecha,
       pr.nombre_canonico as producto,
       m.codigo           as mercado,
       p.precio_hoy_kg    as precio_kg,
       p.masa_hoy         as masa_t,
       p.unidad,
       p.equiv_kg,
       p.tendencia
  from precios_diarios p
  join productos pr on pr.id = p.producto_id
  join mercados  m  on m.id  = p.mercado_id
 order by p.fecha, pr.nombre_canonico
"""


def main(argv=None) -> int:
    db = (argv or sys.argv[1:] or [DB])[0]
    if not os.path.exists(db):
        print(f"ERROR: no existe {db}", file=sys.stderr)
        return 1
    con = sqlite3.connect(db)
    filas = con.execute(CONSULTA).fetchall()
    cols = [d[0] for d in con.execute(CONSULTA).description]
    con.close()

    with open(SALIDA, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(filas)

    mb = os.path.getsize(SALIDA) / 1024 / 1024
    fechas = {r[0] for r in filas}
    print(f"escrito {SALIDA}  ({mb:.1f} MB)")
    print(f"  filas     {len(filas):,}")
    print(f"  columnas  {', '.join(cols)}")
    print(f"  rango     {min(fechas)} -> {max(fechas)}  ({len(fechas)} fechas)")
    print(f"  mercados  {sorted({r[2] for r in filas})}")
    print()
    print("Subelo en Databricks con: Catalog -> Create table -> Upload file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
