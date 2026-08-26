# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Medallón con el dato real del GMML
# MAGIC
# MAGIC 38.481 filas en una herramienta pensada para terabytes. **Es
# MAGIC desproporcionado y se dice así.** Se hace igualmente porque recorrer el
# MAGIC camino completo con dato propio enseña dónde están los bordes, y porque el
# MAGIC vocabulario de esta pieza es el que preguntan en entrevista.
# MAGIC
# MAGIC ### Antes de correr esto
# MAGIC
# MAGIC Sube `lakehouse/gmml_real.csv` con **Catalog → Create table → Upload file**,
# MAGIC a `workspace.default.gmml_real`. Son 2,1 MB.
# MAGIC
# MAGIC ### Lo que se midió en el notebook 00, y que condiciona esto
# MAGIC
# MAGIC | | |
# MAGIC |---|---|
# MAGIC | generar filas | efectivamente gratis, no es el cuello de botella |
# MAGIC | **escribir en Delta** | **77 K filas/s** ← esto sí cuesta |
# MAGIC | `shuffle.partitions` | `auto`: AQE decide, tú no |
# MAGIC | dato real a escala | `samples.tpch.lineitem`, 30 M filas |
# MAGIC
# MAGIC Con 38.481 filas todo esto va a tardar segundos. Ése es el punto.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Silenciar el ruido de Spark Connect
# MAGIC
# MAGIC En el notebook 00, una tabla que no existía imprimió **tres volcados GRPC
# MAGIC completos** antes de llegar al mensaje útil. Ese ruido no es información:
# MAGIC entierra la línea que sí importa.

# COMMAND ----------

import logging
import time

logging.getLogger("pyspark.sql.connect.logging").setLevel(logging.CRITICAL)

CATALOGO = "workspace"
ORIGEN = f"{CATALOGO}.default.gmml_real"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Los tres esquemas
# MAGIC
# MAGIC En Unity Catalog el namespace es `catalogo.esquema.tabla`. Las tres capas
# MAGIC del medallón son **tres esquemas**, no tres carpetas:
# MAGIC
# MAGIC | capa | qué garantiza | qué NO garantiza |
# MAGIC |---|---|---|
# MAGIC | `bronze` | el dato tal como llegó, con su procedencia | que sea correcto |
# MAGIC | `silver` | tipado, validado, deduplicado | que responda una pregunta de negocio |
# MAGIC | `gold` | agregado y listo para consumo | nada más, es el final |
# MAGIC
# MAGIC La columna de la derecha es la que se olvida. Bronze **no** limpia. Si
# MAGIC bronze corrige algo, ya no puedes reconstruir lo que la fuente publicó.

# COMMAND ----------

for capa in ("bronze", "silver", "gold"):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOGO}.{capa}")
    print(f"  esquema {CATALOGO}.{capa} listo")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Bronze: el dato crudo, con procedencia
# MAGIC
# MAGIC Bronze añade **una sola cosa** al CSV: de dónde salió y cuándo entró. Esas
# MAGIC dos columnas son la diferencia entre una tabla y un archivo.
# MAGIC
# MAGIC Nada más se toca. Los tipos siguen como vinieron.

# COMMAND ----------

from pyspark.sql import functions as F

t0 = time.time()
bronze = (spark.table(ORIGEN)
          .withColumn("_origen", F.lit("preciovivo/data/preciovivo.db"))
          .withColumn("_ingerido_en", F.current_timestamp()))

bronze.write.mode("overwrite").saveAsTable(f"{CATALOGO}.bronze.gmml")
print(f"  bronze escrita en {time.time() - t0:.1f} s")
display(spark.sql(f"SELECT * FROM {CATALOGO}.bronze.gmml LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Silver: tipos declarados y reglas del dominio
# MAGIC
# MAGIC Aquí es donde el CSV deja de ser texto. Y donde se aplican las reglas que
# MAGIC el pipeline ya tiene:
# MAGIC
# MAGIC - `precio_kg` entre 0,2 y 60 soles: fuera de eso es un error de parseo, no
# MAGIC   un precio raro
# MAGIC - clave única `(fecha, producto, mercado)`
# MAGIC - los mercados **no se mezclan**: GMML publica precio de cierre, MMF2
# MAGIC   promedio semanal. Mismo nombre, magnitudes distintas
# MAGIC
# MAGIC **Lo que no cumple no se descarta en silencio: va a cuarentena.** Un dato
# MAGIC que desaparece sin dejar rastro es el fallo que este proyecto lleva un
# MAGIC postmortem entero persiguiendo.

# COMMAND ----------

PRECIO_MIN, PRECIO_MAX = 0.2, 60.0

crudo = spark.table(f"{CATALOGO}.bronze.gmml").select(
    F.to_date("fecha").alias("fecha"),
    F.col("producto").cast("string"),
    F.col("mercado").cast("string"),
    F.col("precio_kg").cast("double"),
    F.col("masa_t").cast("double"),
    F.col("unidad").cast("string"),
    F.col("equiv_kg").cast("double"),
    F.col("_ingerido_en"),
)

valido = (F.col("fecha").isNotNull()
          & F.col("precio_kg").isNotNull()
          & F.col("precio_kg").between(PRECIO_MIN, PRECIO_MAX))

limpio = crudo.filter(valido)
sucio = crudo.filter(~valido).withColumn(
    "_motivo",
    F.when(F.col("fecha").isNull(), "fecha ilegible")
     .when(F.col("precio_kg").isNull(), "precio ausente")
     .otherwise(F.concat(F.lit("precio fuera de ["),
                         F.lit(str(PRECIO_MIN)), F.lit(", "),
                         F.lit(str(PRECIO_MAX)), F.lit("]"))))

t0 = time.time()
limpio.write.mode("overwrite").saveAsTable(f"{CATALOGO}.silver.precios")
sucio.write.mode("overwrite").saveAsTable(f"{CATALOGO}.silver.cuarentena")
print(f"  silver escrita en {time.time() - t0:.1f} s")

n_ok = spark.table(f"{CATALOGO}.silver.precios").count()
n_mal = spark.table(f"{CATALOGO}.silver.cuarentena").count()
print(f"  aceptadas   {n_ok:,}")
print(f"  cuarentena  {n_mal:,}  ({100 * n_mal / max(n_ok + n_mal, 1):.2f} %)")
if n_mal:
    display(spark.sql(
        f"SELECT _motivo, count(*) n FROM {CATALOGO}.silver.cuarentena "
        f"GROUP BY _motivo ORDER BY n DESC"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### La comprobación que no se puede saltar
# MAGIC
# MAGIC Si la clave `(fecha, producto, mercado)` no es única, silver está mintiendo
# MAGIC sobre su propia granularidad y todo lo que se agregue encima sale mal.

# COMMAND ----------

dup = spark.sql(f"""
    SELECT fecha, producto, mercado, count(*) n
      FROM {CATALOGO}.silver.precios
     GROUP BY fecha, producto, mercado
    HAVING count(*) > 1
""")
n_dup = dup.count()
print(f"  claves duplicadas: {n_dup}")
if n_dup:
    print("  SILVER NO CUMPLE SU PROPIA GRANULARIDAD")
    display(dup.limit(10))
else:
    print("  la clave es unica: silver puede agregarse con confianza")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Gold: una tabla por pregunta
# MAGIC
# MAGIC Gold no es "silver pero resumida". Es **una tabla por pregunta que alguien
# MAGIC hace de verdad**. Si nadie la consulta, no debería existir.
# MAGIC
# MAGIC Dos aquí, y las dos separan por mercado en vez de sumarlos, porque mezclar
# MAGIC precio de cierre con promedio semanal produce un número que no significa
# MAGIC nada.

# COMMAND ----------

t0 = time.time()

spark.sql(f"""
CREATE OR REPLACE TABLE {CATALOGO}.gold.resumen_mensual AS
SELECT mercado,
       date_trunc('month', fecha)      AS mes,
       count(DISTINCT producto)        AS productos,
       count(*)                        AS observaciones,
       round(avg(precio_kg), 4)        AS precio_medio,
       round(percentile(precio_kg, 0.5), 4) AS precio_mediano,
       round(sum(masa_t), 1)           AS masa_total
  FROM {CATALOGO}.silver.precios
 GROUP BY mercado, date_trunc('month', fecha)
""")

spark.sql(f"""
CREATE OR REPLACE TABLE {CATALOGO}.gold.volatilidad_producto AS
SELECT mercado, producto,
       count(*)                          AS dias_con_dato,
       round(min(precio_kg), 4)          AS minimo,
       round(max(precio_kg), 4)          AS maximo,
       round(avg(precio_kg), 4)          AS promedio,
       round(stddev(precio_kg), 4)       AS desviacion,
       round(stddev(precio_kg) / avg(precio_kg), 4) AS coef_variacion
  FROM {CATALOGO}.silver.precios
 GROUP BY mercado, producto
HAVING count(*) >= 30
""")

print(f"  gold escrita en {time.time() - t0:.1f} s")

# COMMAND ----------

display(spark.sql(f"""
    SELECT * FROM {CATALOGO}.gold.resumen_mensual
     ORDER BY mercado, mes DESC LIMIT 12
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Los diez más volátiles del GMML
# MAGIC
# MAGIC Coeficiente de variación: desviación dividida por la media. Adimensional, y
# MAGIC por eso comparable entre un producto de S/ 1 y otro de S/ 20. La desviación
# MAGIC a secas premiaría siempre a los caros.
# MAGIC
# MAGIC **Esto sí es dato real, así que la conclusión vale.**

# COMMAND ----------

display(spark.sql(f"""
    SELECT producto, dias_con_dato, promedio, desviacion, coef_variacion
      FROM {CATALOGO}.gold.volatilidad_producto
     WHERE mercado = 'GMML'
     ORDER BY coef_variacion DESC LIMIT 10
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Lo que Delta trae de regalo y un archivo no
# MAGIC
# MAGIC Cada escritura crea una **versión**. Puedes consultar la tabla como estaba
# MAGIC hace tres escrituras sin haber hecho copias. Eso es viaje en el tiempo, y es
# MAGIC la mitad de la razón por la que existe el formato.
# MAGIC
# MAGIC La otra mitad es que `DESCRIBE DETAIL` te dice cuántos archivos hay debajo.
# MAGIC **Muchos archivos pequeños es el problema de rendimiento más común de un
# MAGIC lakehouse**, y a esta escala se ve venir.

# COMMAND ----------

display(spark.sql(f"DESCRIBE HISTORY {CATALOGO}.silver.precios"))

# COMMAND ----------

for tabla in ("bronze.gmml", "silver.precios", "gold.resumen_mensual",
              "gold.volatilidad_producto"):
    d = spark.sql(f"DESCRIBE DETAIL {CATALOGO}.{tabla}").collect()[0].asDict()
    n = spark.table(f"{CATALOGO}.{tabla}").count()
    bytes_ = d.get("sizeInBytes") or 0
    print(f"  {tabla:26s} {n:>8,} filas · {d.get('numFiles')} archivo(s) · "
          f"{bytes_ / 1024:>9,.0f} KB · {bytes_ / max(n, 1):.0f} bytes/fila")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Linaje
# MAGIC
# MAGIC Unity Catalog registra solo qué tabla salió de cuál. Nadie lo declaró: lo
# MAGIC dedujo de las consultas.
# MAGIC
# MAGIC **Ése es el problema que Unity Catalog resuelve**, y por eso no tiene
# MAGIC sentido con cinco tablas y una persona. Con tres mil tablas y cincuenta
# MAGIC personas, es la diferencia entre poder cambiar algo y no atreverse.

# COMMAND ----------

try:
    display(spark.sql(f"""
        SELECT source_table_full_name, target_table_full_name, event_time
          FROM system.access.table_lineage
         WHERE target_table_full_name LIKE '{CATALOGO}.%'
         ORDER BY event_time DESC LIMIT 20
    """))
except Exception as e:                       # noqa: BLE001
    print(f"  linaje no accesible: {type(e).__name__}")
    print("  (las tablas de system.access pueden tardar en poblarse o estar "
          "restringidas en Free Edition; no bloquea nada)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Lo que hay que anotar
# MAGIC
# MAGIC - filas aceptadas contra filas en cuarentena, **con el motivo**
# MAGIC - claves duplicadas: tiene que ser 0
# MAGIC - **bytes por fila** de cada tabla, que es lo que permite proyectar la
# MAGIC   prueba de carga del notebook 02
# MAGIC - cuántos archivos genera cada escritura
# MAGIC
# MAGIC Pega la salida. Con los bytes por fila reales se calcula cuántas filas
# MAGIC hacen falta para cada escalón de la curva, en vez de estimarlo.
