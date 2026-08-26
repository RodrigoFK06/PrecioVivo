# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · OPTIMIZE y Z-ORDER, con el antes y el después
# MAGIC
# MAGIC El notebook 02 encontró esto en `samples.tpcds_sf1.store_sales`:
# MAGIC
# MAGIC ```
# MAGIC 2.879.789 filas · 0,120 GB · 1.824 archivos · 0,1 MB/archivo
# MAGIC ```
# MAGIC
# MAGIC **66 KB por archivo**, contra los ~128 MB que Databricks recomienda. 1.824
# MAGIC aperturas de archivo para leer 120 MB.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### La trampa de este experimento, dicha por delante
# MAGIC
# MAGIC `samples` es un **Delta Share de solo lectura**: no se puede optimizar. Hay
# MAGIC que copiar la tabla al catálogo propio.
# MAGIC
# MAGIC Y ahí está el problema: **al copiarla, Spark la escribiría bien de entrada** y
# MAGIC no quedaría nada que arreglar. El "antes" desaparecería solo.
# MAGIC
# MAGIC Así que la fragmentación **se reproduce a propósito** con
# MAGIC `repartition(1824)`, para que el punto de partida sea el mismo que se midió
# MAGIC en el original. Es una condición fabricada, no encontrada, y decirlo es la
# MAGIC diferencia entre un experimento y una demostración amañada.
# MAGIC
# MAGIC ### Qué separa este diseño
# MAGIC
# MAGIC Tres estados de la MISMA tabla con los MISMOS datos:
# MAGIC
# MAGIC | estado | qué cambia |
# MAGIC |---|---|
# MAGIC | 1 · fragmentada | 1.824 archivos diminutos |
# MAGIC | 2 · `OPTIMIZE` | **solo compacta**: menos archivos, mismo orden |
# MAGIC | 3 · `+ ZORDER` | además **agrupa por la columna del filtro** |
# MAGIC
# MAGIC Dos consultas, elegidas para separar los dos efectos:
# MAGIC
# MAGIC | consulta | qué debería ganar |
# MAGIC |---|---|
# MAGIC | **P · escaneo completo** | solo con la compactación: no hay filtro que podar |
# MAGIC | **F · filtro por fecha** | con la compactación **y** con el Z-ORDER |
# MAGIC
# MAGIC Si F mejora con Z-ORDER y P no, el efecto es de **poda** y no de tamaño de
# MAGIC archivo. Sin las dos consultas no se pueden distinguir.

# COMMAND ----------

import json
import logging
import time

logging.getLogger("pyspark.sql.connect.logging").setLevel(logging.CRITICAL)

CATALOGO = "preciovivo"
ESQUEMA = f"{CATALOGO}.bench"
TABLA = f"{ESQUEMA}.ss_sf1"
ORIGEN = "samples.tpcds_sf1.store_sales"
N_FRAGMENTOS = 1824          # el numero de archivos del original
MEDIDAS = []

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Reproducir la fragmentación
# MAGIC
# MAGIC `repartition(1824)` reparte las filas en 1.824 particiones, y cada una acaba
# MAGIC en su propio archivo. Es exactamente la patología que se quiere medir.

# COMMAND ----------

t0 = time.time()
(spark.table(ORIGEN)
      .repartition(N_FRAGMENTOS)
      .write.mode("overwrite")
      .option("overwriteSchema", "true")
      .saveAsTable(TABLA))
print(f"  tabla fragmentada escrita en {time.time() - t0:.1f} s")

# COMMAND ----------


def detalle(etiqueta: str) -> dict:
    d = spark.sql(f"DESCRIBE DETAIL {TABLA}").collect()[0].asDict()
    b, nf = d.get("sizeInBytes") or 0, d.get("numFiles") or 0
    info = {"estado": etiqueta, "bytes": b, "archivos": nf,
            "mb_por_archivo": round(b / max(nf, 1) / 1e6, 3)}
    print(f"  {etiqueta:22s} {nf:>6,} archivos · {b / 1e6:8.1f} MB · "
          f"{info['mb_por_archivo']:7.3f} MB/archivo")
    return info

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Las dos consultas
# MAGIC
# MAGIC `F` filtra **directamente sobre la columna del hecho**, sin join. Es
# MAGIC deliberado: con un join, la poda dependería de *dynamic file pruning* y no
# MAGIC se sabría si el mérito es del Z-ORDER o del optimizador. Un predicado
# MAGIC directo aísla el efecto.

# COMMAND ----------

COL_FILTRO = "ss_sold_date_sk"

CONSULTAS = {
    "P · escaneo completo": f"""
        SELECT ss_store_sk, count(*) AS n, round(sum(ss_net_paid), 2) AS total
          FROM {TABLA}
         WHERE ss_store_sk IS NOT NULL
         GROUP BY ss_store_sk ORDER BY total DESC LIMIT 20
    """,
    "F · filtro por fecha": f"""
        SELECT ss_store_sk, count(*) AS n, round(sum(ss_net_paid), 2) AS total
          FROM {TABLA}
         WHERE {COL_FILTRO} BETWEEN 2451545 AND 2451910
           AND ss_store_sk IS NOT NULL
         GROUP BY ss_store_sk ORDER BY total DESC LIMIT 20
    """,
}


def medir(etiqueta: str) -> dict:
    """Frio y caliente por consulta, igual que en el notebook 02."""
    fuera = {"estado": etiqueta}
    for nombre, sql in CONSULTAS.items():
        ts = []
        for _ in (1, 2):
            t0 = time.time()
            spark.sql(sql).collect()
            ts.append(time.time() - t0)
        fuera[nombre] = {"frio": round(ts[0], 3), "caliente": round(ts[1], 3)}
        print(f"    {nombre:24s} frio {ts[0]:6.2f}s · caliente {ts[1]:6.2f}s")
    return fuera

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Estado 1 · fragmentada

# COMMAND ----------

print("=== 1. FRAGMENTADA ===")
d1 = detalle("1 fragmentada")
m1 = medir("1 fragmentada")
MEDIDAS.append({**d1, **m1})

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Estado 2 · solo compactar
# MAGIC
# MAGIC `OPTIMIZE` sin `ZORDER` junta archivos pequeños en grandes y **no cambia el
# MAGIC orden de las filas**. Todo lo que mejore aquí es mérito de la compactación
# MAGIC sola.

# COMMAND ----------

print("=== 2. OPTIMIZE (solo compactar) ===")
t0 = time.time()
res = spark.sql(f"OPTIMIZE {TABLA}").collect()
print(f"  OPTIMIZE tardo {time.time() - t0:.1f} s")
try:
    met = res[0].asDict().get("metrics")
    print(f"  metricas: {str(met)[:300]}")
except Exception as e:                           # noqa: BLE001
    print(f"  (sin metricas legibles: {type(e).__name__})")

d2 = detalle("2 compactada")
m2 = medir("2 compactada")
MEDIDAS.append({**d2, **m2})

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Estado 3 · compactar y agrupar por la columna del filtro
# MAGIC
# MAGIC `ZORDER BY` reordena las filas para que las que comparten valor de esa
# MAGIC columna acaben en los mismos archivos. Entonces las estadísticas por archivo
# MAGIC (mínimo y máximo) se vuelven estrechas, y Spark puede **descartar archivos
# MAGIC enteros sin abrirlos**.
# MAGIC
# MAGIC Es la misma poda que se observó en el notebook 02, pero aquí provocada a
# MAGIC propósito en vez de encontrada.

# COMMAND ----------

print("=== 3. OPTIMIZE + ZORDER ===")
t0 = time.time()
try:
    spark.sql(f"OPTIMIZE {TABLA} ZORDER BY ({COL_FILTRO})").collect()
    print(f"  ZORDER tardo {time.time() - t0:.1f} s")
    d3 = detalle("3 z-ordenada")
    m3 = medir("3 z-ordenada")
    MEDIDAS.append({**d3, **m3})
except Exception as e:                           # noqa: BLE001
    print(f"  ZORDER no disponible: {type(e).__name__}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. La tabla que responde la pregunta

# COMMAND ----------

print(f"{'estado':16s} {'archivos':>9s} {'MB/arch':>9s} "
      f"{'P escaneo':>11s} {'F filtrada':>12s}")
base = None
for m in MEDIDAS:
    p = m["P · escaneo completo"]["caliente"]
    f = m["F · filtro por fecha"]["caliente"]
    if base is None:
        base = (p, f)
    print(f"{m['estado']:16s} {m['archivos']:>9,} {m['mb_por_archivo']:>9.3f} "
          f"{p:>10.2f}s {f:>11.2f}s   "
          f"(x{base[0] / p:.2f} · x{base[1] / f:.2f} frente a fragmentada)")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Cómo leerla
# MAGIC
# MAGIC - **P mejora del estado 1 al 2** → la compactación sola ya vale: menos
# MAGIC   aperturas de archivo.
# MAGIC - **F mejora más que P del 2 al 3** → el Z-ORDER está podando, que es un
# MAGIC   efecto distinto del tamaño de archivo.
# MAGIC - **Si F no mejora con Z-ORDER** → o el filtro no es selectivo, o las
# MAGIC   estadísticas por archivo no se están usando. Las dos son respuestas
# MAGIC   válidas y hay que decirlas, no repetir el experimento hasta que salga.
# MAGIC
# MAGIC Los bytes leídos de verdad están en **Query History**, y son la confirmación
# MAGIC de la poda que el tiempo de reloj solo sugiere.

# COMMAND ----------

_salida = json.dumps({"tabla": TABLA, "medidas": MEDIDAS}, ensure_ascii=False)
print(f"(devolviendo {len(_salida):,} bytes de JSON)")
try:
    dbutils.notebook.exit(_salida)
except Exception as _e:                          # noqa: BLE001
    print(f"(sin job que reciba la salida: {type(_e).__name__})")
