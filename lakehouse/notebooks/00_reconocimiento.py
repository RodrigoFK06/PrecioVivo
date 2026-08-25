# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Reconocimiento del entorno
# MAGIC
# MAGIC **Corre esto ANTES de cargar un solo dato.**
# MAGIC
# MAGIC Free Edition no publica sus límites: usa una política de uso justo y, al
# MAGIC pasarte, apaga el workspace **el resto del día**. La cuota es el recurso
# MAGIC escaso, así que hay que gastarla sabiendo contra qué.
# MAGIC
# MAGIC Este notebook no escribe nada y no genera nada persistente. Solo mide qué
# MAGIC hay: versión de Spark, paralelismo real, catálogos accesibles, y el tamaño
# MAGIC de los datasets de `samples` que van a servir de dato real.
# MAGIC
# MAGIC **Pega la salida completa de vuelta al chat.** El diseño de los notebooks
# MAGIC siguientes depende de estos números, no de lo que diga la documentación.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Qué motor hay debajo
# MAGIC
# MAGIC `defaultParallelism` es el número que importa: cuántas tareas puede correr
# MAGIC Spark a la vez. En serverless de Free Edition va a ser pequeño, y es la
# MAGIC razón por la que el objetivo NO es 10 GB sino la curva hasta donde aguante.

# COMMAND ----------

import sys

print("Spark              ", spark.version)
print("Python             ", sys.version.split()[0])
print("paralelismo        ", spark.sparkContext.defaultParallelism)
print("particiones shuffle", spark.conf.get("spark.sql.shuffle.partitions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Qué librerías vienen puestas
# MAGIC
# MAGIC Sin verificación por LinkedIn la salida a internet está restringida, así
# MAGIC que **lo que no venga preinstalado puede no poder instalarse**. Esto lo
# MAGIC comprueba antes de que el plan dependa de ello.

# COMMAND ----------

import importlib

for modulo in ("pyspark", "delta", "mlflow", "pandas", "numpy", "pyarrow"):
    try:
        m = importlib.import_module(modulo)
        print(f"  {modulo:10s} {getattr(m, '__version__', 'sin __version__')}")
    except ImportError:
        print(f"  {modulo:10s} NO DISPONIBLE  <- si el plan lo necesita, hay que rediseñar")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Catálogos accesibles
# MAGIC
# MAGIC Unity Catalog usa tres niveles: `catalogo.esquema.tabla`. Aquí se ve qué
# MAGIC catálogos existen y si `samples` está montado, que es de donde sale el dato
# MAGIC real sin descargar nada.

# COMMAND ----------

display(spark.sql("SHOW CATALOGS"))

# COMMAND ----------

try:
    display(spark.sql("SHOW SCHEMAS IN samples"))
except Exception as e:
    print("samples NO accesible:", type(e).__name__, e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Tamaño real de los datasets de muestra
# MAGIC
# MAGIC Un `count()` sobre una tabla grande ya consume cuota, así que se hace UNA
# MAGIC vez y se anota. Estos números deciden cuál sirve de banco de pruebas con
# MAGIC dato real.

# COMMAND ----------

CANDIDATOS = [
    "samples.nyctaxi.trips",
    "samples.tpch.lineitem",
    "samples.tpch.orders",
    "samples.tpcds_sf1.store_sales",
]

for t in CANDIDATOS:
    try:
        df = spark.table(t)
        n = df.count()
        print(f"  {t:32s} {n:>12,} filas · {len(df.columns):2d} columnas")
    except Exception as e:
        print(f"  {t:32s} no accesible ({type(e).__name__})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Cuánto tarda Spark en fabricar filas
# MAGIC
# MAGIC Es la operación sobre la que se apoya todo el generador: `range()` produce
# MAGIC filas sin leer nada de disco. Esto mide el techo de generación antes de
# MAGIC intentar los gigabytes.
# MAGIC
# MAGIC Sube en escalones y **se para solo** si uno tarda de más, para no quemar la
# MAGIC cuota del día en la primera prueba.

# COMMAND ----------

import time

TOPE_SEGUNDOS = 45  # si un escalon pasa de aqui, no se intenta el siguiente

for exp in (6, 7, 8, 9):          # 1M, 10M, 100M, 1000M filas
    n = 10 ** exp
    t0 = time.time()
    try:
        total = (spark.range(n)
                 .selectExpr("id", "rand() as u", "id % 200 as producto")
                 .filter("u < 0.5").count())
        dt = time.time() - t0
        print(f"  {n:>13,} filas  {dt:7.1f} s   {n / dt / 1e6:6.1f} M filas/s"
              f"   (filtradas {total:,})")
        if dt > TOPE_SEGUNDOS:
            print(f"  -> por encima de {TOPE_SEGUNDOS} s: se para aqui a proposito")
            break
    except Exception as e:
        print(f"  {n:>13,} filas  FALLO: {type(e).__name__}: {str(e)[:140]}")
        break

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Lo que hay que anotar
# MAGIC
# MAGIC Copia la salida completa. Con ella se decide:
# MAGIC
# MAGIC - **el paralelismo** → cuántas particiones tiene sentido pedir
# MAGIC - **qué dataset de `samples`** sirve de dato real y a qué escala
# MAGIC - **el techo de generación** → hasta qué escalón de la curva es realista
# MAGIC - **si falta alguna librería** → si hay que rediseñar algo
# MAGIC
# MAGIC Nada de esto se supone. Todo se mide una vez y se escribe.
