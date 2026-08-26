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
# MAGIC Este notebook no escribe nada persistente. Solo mide qué hay.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Corrección respecto a la primera versión
# MAGIC
# MAGIC La v1 murió en la celda 1 con:
# MAGIC
# MAGIC ```
# MAGIC [JVM_ATTRIBUTE_NOT_SUPPORTED] SparkContext is not supported on serverless compute
# MAGIC ```
# MAGIC
# MAGIC **Serverless usa Spark Connect**: el notebook es un cliente ligero que habla
# MAGIC con un motor remoto, así que no existe un `SparkContext` local al que
# MAGIC preguntarle nada. Todo lo que empiece por `spark.sparkContext` está fuera.
# MAGIC
# MAGIC Y había un fallo peor que ese: el notebook **se detuvo en la primera celda**
# MAGIC y no midió nada más. Un diagnóstico que muere al primer tropiezo no
# MAGIC diagnostica. Ahora cada sonda va aislada: si una falla, lo dice y las demás
# MAGIC siguen.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Andamio: sondas que no se tumban entre sí

# COMMAND ----------

import time
import traceback

RESUMEN = {}


def sonda(nombre):
    """Ejecuta una sonda, la cronometra, y si revienta lo REPORTA y sigue.

    Es la misma regla que el resto del proyecto: un fallo que no deja rastro no
    existe, pero un fallo que detiene todo lo demas tampoco informa.
    """
    def envoltura(fn):
        print(f"\n=== {nombre} " + "=" * max(0, 58 - len(nombre)))
        t0 = time.time()
        try:
            fn()
            RESUMEN[nombre] = "ok"
        except Exception as e:                       # noqa: BLE001
            RESUMEN[nombre] = f"{type(e).__name__}"
            print(f"  FALLO {type(e).__name__}: {str(e)[:220]}")
            traceback.print_exc(limit=1)
        finally:
            print(f"  ({time.time() - t0:.1f} s)")
        return fn
    return envoltura

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Qué motor hay debajo
# MAGIC
# MAGIC Sin `SparkContext`, lo que queda es la configuración SQL. En serverless
# MAGIC muchas de estas salen en `auto`, porque el motor las decide solo con AQE
# MAGIC (Adaptive Query Execution): reparticiona en tiempo de ejecución según lo
# MAGIC que ve, en vez de fijarlo de antemano.
# MAGIC
# MAGIC Que salga `auto` **es información**, no un fallo: significa que el número de
# MAGIC particiones no lo controlas tú, y eso condiciona la prueba de carga.

# COMMAND ----------


@sonda("motor")
def _motor():
    import sys
    print("  Spark  ", spark.version)
    print("  Python ", sys.version.split()[0])
    for clave in ("spark.sql.shuffle.partitions",
                  "spark.sql.adaptive.enabled",
                  "spark.sql.adaptive.coalescePartitions.enabled",
                  "spark.sql.files.maxPartitionBytes",
                  "spark.databricks.clusterUsageTags.clusterName"):
        try:
            print(f"  {clave:52s} {spark.conf.get(clave)}")
        except Exception as e:                       # noqa: BLE001
            print(f"  {clave:52s} (no expuesto: {type(e).__name__})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Qué librerías vienen puestas
# MAGIC
# MAGIC Sin verificación por LinkedIn la salida a internet está restringida, así
# MAGIC que **lo que no venga preinstalado puede no poder instalarse**. Esto lo
# MAGIC comprueba antes de que el plan dependa de ello.

# COMMAND ----------


@sonda("librerias")
def _libs():
    import importlib
    for modulo in ("pyspark", "delta", "mlflow", "pandas", "numpy", "pyarrow"):
        try:
            m = importlib.import_module(modulo)
            print(f"  {modulo:10s} {getattr(m, '__version__', 'sin __version__')}")
        except ImportError:
            print(f"  {modulo:10s} NO DISPONIBLE")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Catálogos accesibles
# MAGIC
# MAGIC Unity Catalog usa tres niveles: `catalogo.esquema.tabla`. Aquí se ve qué
# MAGIC catálogos hay y si `samples` está montado, que es de donde sale el dato real
# MAGIC sin descargar nada.

# COMMAND ----------


@sonda("catalogos")
def _cat():
    for fila in spark.sql("SHOW CATALOGS").collect():
        print("  ", fila[0])

# COMMAND ----------


@sonda("esquemas de samples")
def _sch():
    for fila in spark.sql("SHOW SCHEMAS IN samples").collect():
        print("  ", fila[0])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Tamaño real de los datasets de muestra
# MAGIC
# MAGIC Un `count()` sobre una tabla grande ya consume cuota, así que se hace UNA
# MAGIC vez y se anota. Cada tabla va por separado: si una no existe, las demás se
# MAGIC miden igual.

# COMMAND ----------


@sonda("tamano de samples")
def _tam():
    candidatos = ["samples.nyctaxi.trips",
                  "samples.tpch.lineitem",
                  "samples.tpch.orders",
                  "samples.tpcds_sf1.store_sales"]
    for t in candidatos:
        try:
            df = spark.table(t)
            t0 = time.time()
            n = df.count()
            print(f"  {t:32s} {n:>12,} filas · {len(df.columns):2d} col "
                  f"· count en {time.time() - t0:.1f} s")
        except Exception as e:                       # noqa: BLE001
            print(f"  {t:32s} no accesible ({type(e).__name__})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Cuánto tarda Spark en fabricar filas
# MAGIC
# MAGIC Es la operación sobre la que se apoya el generador: `range()` produce filas
# MAGIC sin leer nada de disco.
# MAGIC
# MAGIC **Aquí está el número que reemplaza al paralelismo que no podemos leer.** Si
# MAGIC el rendimiento crece de forma lineal al subir de escalón, el motor no está
# MAGIC saturado. Cuando deja de crecer, ahí está el techo. Eso vale más que un
# MAGIC `defaultParallelism` porque es lo que de verdad vas a obtener.
# MAGIC
# MAGIC Sube en escalones y **se para solo** si uno tarda de más, para no quemar la
# MAGIC cuota del día en la primera prueba.

# COMMAND ----------


@sonda("techo de generacion")
def _gen():
    tope_segundos = 45
    for exp in (6, 7, 8, 9):          # 1M, 10M, 100M, 1000M filas
        n = 10 ** exp
        t0 = time.time()
        try:
            total = (spark.range(n)
                     .selectExpr("id", "rand() as u", "id % 200 as producto")
                     .filter("u < 0.5").count())
            dt = time.time() - t0
            print(f"  {n:>13,} filas  {dt:7.1f} s  {n / dt / 1e6:7.1f} M filas/s"
                  f"  (quedan {total:,})")
            if dt > tope_segundos:
                print(f"  -> por encima de {tope_segundos} s: se para aqui a proposito")
                break
        except Exception as e:                       # noqa: BLE001
            print(f"  {n:>13,} filas  FALLO {type(e).__name__}: {str(e)[:140]}")
            break

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Escritura Delta, que es lo que de verdad va a costar
# MAGIC
# MAGIC Generar filas en memoria es barato. **Escribirlas como tabla Delta es lo que
# MAGIC cuesta**, y es lo que hará la prueba de carga de verdad. Esto lo mide con
# MAGIC una tabla pequeña y la borra.
# MAGIC
# MAGIC También confirma de paso que se puede crear un catálogo o esquema propio,
# MAGIC que es requisito para todo lo que viene después.

# COMMAND ----------


@sonda("escritura Delta")
def _delta():
    spark.sql("CREATE SCHEMA IF NOT EXISTS workspace.sonda")
    df = (spark.range(1_000_000)
          .selectExpr("id", "rand() as precio", "id % 200 as producto"))
    t0 = time.time()
    df.write.mode("overwrite").saveAsTable("workspace.sonda.prueba")
    dt = time.time() - t0
    print(f"  1.000.000 filas escritas en Delta: {dt:.1f} s "
          f"({1e6 / dt / 1e3:.0f} K filas/s)")
    for fila in spark.sql("DESCRIBE DETAIL workspace.sonda.prueba").collect():
        d = fila.asDict()
        print(f"  archivos {d.get('numFiles')}  "
              f"bytes {d.get('sizeInBytes'):,}  formato {d.get('format')}")
    spark.sql("DROP TABLE IF EXISTS workspace.sonda.prueba")
    print("  (tabla de prueba borrada)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Resumen
# MAGIC
# MAGIC Qué sondas pasaron y cuáles no. **Copia esto y la salida de arriba.**

# COMMAND ----------

print("resumen de sondas:")
for k, v in RESUMEN.items():
    print(f"  {k:24s} {v}")
fallidas = [k for k, v in RESUMEN.items() if v != "ok"]
print()
print("todas ok" if not fallidas else f"fallaron: {fallidas}")
