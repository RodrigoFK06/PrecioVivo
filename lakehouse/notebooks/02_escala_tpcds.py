# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · La curva de escala, con dato real a 1000×
# MAGIC
# MAGIC `samples.tpcds_sf1` y `samples.tpcds_sf1000` son **el mismo esquema, las
# MAGIC mismas 24 tablas, mil veces más datos**. Eso es un experimento controlado
# MAGIC servido en bandeja: mismas consultas, misma forma, solo cambia el volumen.
# MAGIC
# MAGIC Mejor que generar datos sintéticos, porque nadie puede acusar al benchmark
# MAGIC de estar hecho a medida. TPC-DS es un estándar de la industria.
# MAGIC
# MAGIC ### Lo que se pregunta
# MAGIC
# MAGIC > Al multiplicar los datos por 1000, ¿por cuánto se multiplica el tiempo?
# MAGIC
# MAGIC - **×1000** → el motor está saturado, no hay paralelismo que ganar
# MAGIC - **×100** → Spark está absorbiendo parte con paralelismo
# MAGIC - **×2000** → algo se degrada: memoria, derrame a disco, sesgo de partición
# MAGIC
# MAGIC Esa razón es el resultado. No "tardó X segundos".
# MAGIC
# MAGIC ### Dos correcciones que trae este notebook
# MAGIC
# MAGIC 1. **`SHOW SCHEMAS` sobre un Delta Share miente.** Devolvió 6 de los 11
# MAGIC    esquemas de `samples`, y escribí "tpcds_sf1 no existe" fiándome de eso.
# MAGIC    `SHOW TABLES IN <esquema>` sí funciona. Aquí no se enumera nada: se
# MAGIC    pregunta por la tabla concreta.
# MAGIC 2. **`count()` sobre Delta no mide nada.** Lee el log de transacciones, no
# MAGIC    los datos. Por eso 30 M filas "se contaron" en 1,3 s en el notebook 00.
# MAGIC    Aquí toda consulta obliga a escanear de verdad.
# MAGIC
# MAGIC ### Protección de la cuota
# MAGIC
# MAGIC Free Edition apaga el workspace **el resto del día** si te pasas. Este
# MAGIC notebook tiene presupuesto: si una consulta supera `TOPE_SEGUNDOS`, **no se
# MAGIC intenta la siguiente a esa escala**. Perder una medición es barato; perder
# MAGIC el día no.

# COMMAND ----------

import logging
import time

logging.getLogger("pyspark.sql.connect.logging").setLevel(logging.CRITICAL)

TOPE_SEGUNDOS = 180        # por consulta; al superarlo se abandona esa escala
ESCALAS = ("tpcds_sf1", "tpcds_sf1000")
RESULTADOS = []

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Cuánto pesa cada escala, sin escanear nada
# MAGIC
# MAGIC `DESCRIBE DETAIL` lee metadatos del log de Delta. Es gratis, y da lo que
# MAGIC hace falta para interpretar todo lo demás: bytes, número de archivos y
# MAGIC tamaño medio de archivo.
# MAGIC
# MAGIC **El tamaño medio de archivo importa más de lo que parece.** Muchos archivos
# MAGIC pequeños obligan a Spark a abrir miles de veces; pocos archivos enormes
# MAGIC impiden repartir el trabajo. Es el problema de rendimiento más común de un
# MAGIC lakehouse y se ve aquí antes de ejecutar una sola consulta.

# COMMAND ----------

TABLAS = ["store_sales", "item", "date_dim", "store", "customer"]

for esquema in ESCALAS:
    print(f"\n=== samples.{esquema} " + "=" * 40)
    total_bytes = 0
    for t in TABLAS:
        nombre = f"samples.{esquema}.{t}"
        try:
            d = spark.sql(f"DESCRIBE DETAIL {nombre}").collect()[0].asDict()
            n = spark.table(nombre).count()          # metadatos, no escaneo
            b = d.get("sizeInBytes") or 0
            nf = d.get("numFiles") or 0
            total_bytes += b
            print(f"  {t:14s} {n:>14,} filas · {b / 1e9:8.3f} GB · "
                  f"{nf:>5} archivos · {b / max(nf, 1) / 1e6:6.1f} MB/archivo")
        except Exception as e:                       # noqa: BLE001
            print(f"  {t:14s} no accesible ({type(e).__name__})")
    print(f"  {'TOTAL':14s} {total_bytes / 1e9:>8.3f} GB")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Tres consultas, de barata a cara
# MAGIC
# MAGIC No son inventadas: son la forma de las consultas de TPC-DS.
# MAGIC
# MAGIC | | qué ejercita | por qué está |
# MAGIC |---|---|---|
# MAGIC | **A** · agregación sobre el hecho | escaneo completo + shuffle | el suelo: sin joins |
# MAGIC | **B** · join hecho–dimensión | broadcast join | el caso que Spark optimiza bien |
# MAGIC | **C** · star join con filtro | dos joins + filtro + agrupación | el caso realista de un almacén |
# MAGIC
# MAGIC Cada una termina en `LIMIT` pequeño y `.collect()`: el resultado cabe en el
# MAGIC cliente, pero **para producirlo hay que escanear el hecho entero**. Así se
# MAGIC mide trabajo, no latencia de red.

# COMMAND ----------

CONSULTAS = {
    "A · agregacion sobre el hecho": """
        SELECT ss_store_sk,
               count(*)                       AS n,
               round(sum(ss_net_paid), 2)     AS total
          FROM samples.{esq}.store_sales
         WHERE ss_store_sk IS NOT NULL
         GROUP BY ss_store_sk
         ORDER BY total DESC
         LIMIT 20
    """,
    "B · join hecho-dimension": """
        SELECT i.i_category,
               count(*)                          AS n,
               round(sum(ss.ss_net_paid), 2)     AS total
          FROM samples.{esq}.store_sales ss
          JOIN samples.{esq}.item i ON ss.ss_item_sk = i.i_item_sk
         WHERE i.i_category IS NOT NULL
         GROUP BY i.i_category
         ORDER BY total DESC
         LIMIT 20
    """,
    "C · star join con filtro": """
        SELECT d.d_year, d.d_moy, i.i_category,
               round(sum(ss.ss_net_paid), 2)     AS total
          FROM samples.{esq}.store_sales ss
          JOIN samples.{esq}.date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
          JOIN samples.{esq}.item i     ON ss.ss_item_sk      = i.i_item_sk
         WHERE d.d_year = 2001 AND i.i_category IS NOT NULL
         GROUP BY d.d_year, d.d_moy, i.i_category
         ORDER BY total DESC
         LIMIT 20
    """,
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. La medición
# MAGIC
# MAGIC Cada consulta se corre **dos veces**. La primera incluye planificación,
# MAGIC apertura de archivos y calentamiento de caché; la segunda es el régimen
# MAGIC estable. Reportar solo una de las dos engaña, en un sentido o en el otro,
# MAGIC así que se reportan las dos.

# COMMAND ----------


def medir(nombre: str, sql: str, esquema: str) -> dict | None:
    consulta = sql.format(esq=esquema)
    tiempos = []
    filas = 0
    for intento in (1, 2):
        t0 = time.time()
        try:
            filas = len(spark.sql(consulta).collect())
        except Exception as e:                       # noqa: BLE001
            print(f"    FALLO {type(e).__name__}: {str(e)[:160]}")
            return None
        dt = time.time() - t0
        tiempos.append(dt)
        if dt > TOPE_SEGUNDOS:
            print(f"    intento {intento}: {dt:.1f} s  "
                  f"POR ENCIMA DEL TOPE ({TOPE_SEGUNDOS} s)")
            return {"consulta": nombre, "escala": esquema, "frio": tiempos[0],
                    "caliente": None, "filas": filas, "abortado": True}
    print(f"    frio {tiempos[0]:7.1f} s  ·  caliente {tiempos[1]:7.1f} s  "
          f"·  {filas} filas de resultado")
    return {"consulta": nombre, "escala": esquema, "frio": tiempos[0],
            "caliente": tiempos[1], "filas": filas, "abortado": False}

# COMMAND ----------

for esquema in ESCALAS:
    print(f"\n=== samples.{esquema} " + "=" * 40)
    abortar = False
    for nombre, sql in CONSULTAS.items():
        if abortar:
            print(f"  {nombre}\n    saltada: la anterior supero el tope")
            continue
        print(f"  {nombre}")
        r = medir(nombre, sql, esquema)
        if r:
            RESULTADOS.append(r)
            # Si una consulta se pasa del presupuesto, no se intentan las mas
            # caras de esa escala. La cuota vale mas que la medicion que falta.
            abortar = r["abortado"]
        else:
            abortar = True

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. La razón de escala, que es el resultado
# MAGIC
# MAGIC Mil veces más datos. ¿Cuántas veces más tiempo?

# COMMAND ----------

por_consulta = {}
for r in RESULTADOS:
    por_consulta.setdefault(r["consulta"], {})[r["escala"]] = r

print(f"{'consulta':32s} {'SF1':>9s} {'SF1000':>10s} {'razon':>8s}  lectura")
print("-" * 86)
for nombre, d in por_consulta.items():
    a, b = d.get("tpcds_sf1"), d.get("tpcds_sf1000")
    if not (a and b) or a["caliente"] is None or b["caliente"] is None:
        falta = "SF1000 no completo" if a else "SF1 no completo"
        print(f"{nombre:32s} {'-':>9s} {'-':>10s} {'-':>8s}  {falta}")
        continue
    razon = b["caliente"] / max(a["caliente"], 1e-9)
    if razon < 300:
        lectura = "Spark absorbe con paralelismo"
    elif razon < 1500:
        lectura = "escala proporcional: saturado"
    else:
        lectura = "SE DEGRADA: mirar derrame y sesgo"
    print(f"{nombre:32s} {a['caliente']:8.1f}s {b['caliente']:9.1f}s "
          f"{razon:7.0f}x  {lectura}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Dónde ver lo que este notebook no puede medir
# MAGIC
# MAGIC El tiempo de reloj no dice **cuántos bytes se leyeron de verdad**. Y esa es
# MAGIC la diferencia entre "Spark es rápido" y "Spark se saltó el 99 % de los datos
# MAGIC gracias al pruning de particiones", que no es lo mismo en absoluto.
# MAGIC
# MAGIC Ese número está en **Query History** (barra lateral izquierda): busca las
# MAGIC consultas de este notebook y mira *bytes read* y *rows read*.
# MAGIC
# MAGIC En la consulta **C** el filtro `d_year = 2001` debería permitir a Spark
# MAGIC descartar la mayoría de los archivos sin abrirlos. Si los bytes leídos son
# MAGIC parecidos a los de la consulta **A**, el pruning no está funcionando y ahí
# MAGIC hay una optimización real que hacer.
# MAGIC
# MAGIC **Anota esos números.** Son los que convierten esto en una medición.

# COMMAND ----------

for nombre, d in por_consulta.items():
    for esq in ESCALAS:
        r = d.get(esq)
        if r:
            estado = "ABORTADA" if r["abortado"] else "ok"
            cal = "-" if r["caliente"] is None else f"{r['caliente']:.1f}"
            print(f"  {nombre:32s} {esq:14s} frio {r['frio']:7.1f}s  "
                  f"caliente {cal:>7}s  {estado}")
