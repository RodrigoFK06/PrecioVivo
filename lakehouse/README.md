# Lakehouse: dos pistas, una honesta y otra desproporcionada

Este directorio **no es parte del pipeline de producción**. Es un banco de
pruebas para responder una pregunta concreta:

> ¿A partir de qué volumen deja de servir lo que ya tengo, y empieza a compensar
> Spark?

Sin ese número, "usé Databricks" no dice nada. Con él, dice algo que la mayoría
de los portafolios no puede decir.

---

## Las dos pistas

| | pista A · dato real | pista B · carga sintética |
|---|---|---|
| qué entra | los 38.481 precios reales del GMML | series generadas, de 10 MB a lo que aguante |
| para qué | recorrer bronze → silver → gold de verdad | encontrar el punto de ruptura |
| honestidad | conclusiones de negocio permitidas | **solo rendimiento, nunca negocio** |

**La pista A es desproporcionada y se dice así.** 11 MB de datos estructurados en
una herramienta pensada para terabytes. Se hace igualmente porque recorrer el
camino completo con dato real enseña el vocabulario y los bordes; no porque haga
falta.

**La pista B es la que justifica la herramienta.** Y su regla es absoluta: con
datos sintéticos se afirma **throughput**, jamás un hallazgo sobre el mercado
peruano.

---

## El mapa medallón, que ya existía sin llamarse así

La arquitectura ya está implementada en el pipeline. Lo que faltaba era nombrarla.

| capa | qué es hoy en Precio Vivo | qué garantiza |
|---|---|---|
| **landing** | `data/raw/*.pdf` — 571 archivos, 93,8 MB | el documento tal como llegó |
| **bronze** | tabla `fuentes_raw` — url, sha256, bytes, fecha de descarga | trazabilidad y detección de republicación |
| **silver** | `precios_diarios` — 38.481 filas, PK `(producto, mercado, fecha)` | validado contra la compuerta de layout y el rango 0,2–60 S//kg |
| **gold** | `snapshot.json` (3,4 MB) y el índice RAG (2,3 MB) | listo para consumo, sin lógica de negocio pendiente |

**Lo que NO hay, y por qué:** ni Delta Lake, ni Iceberg, ni un catálogo. Delta
resuelve escrituras concurrentes de varios procesos sobre tablas de terabytes.
Aquí hay 7,7 MB y **un** escritor. Meterlo sería infraestructura sin problema que
resolver.

Esa frase es la respuesta de entrevista, no una excusa.

---

## Cómo funciona el generador

El problema práctico: hacen falta gigabytes **dentro** de Databricks, subirlos
desde Lima no es viable, y Free Edition restringe la salida a internet hasta
verificar la cuenta.

La solución: **viajan 13,5 KB, se generan los gigabytes allí dentro.**

```bash
python lakehouse/perfil.py                                   # 13,5 KB
python lakehouse/generar.py --productos 200 --dias 500 --verificar
```

`perfil_gmml.json` describe la **forma** de la serie real: la función inversa de
la distribución de retornos, la de volúmenes, las unidades y la estacionalidad.
No contiene ni un precio real ni una fecha real.

### Por qué bootstrap y no ruido gaussiano

Medido sobre 37.988 retornos diarios reales:

```
desviación   0,0822
curtosis     24,2      (una gaussiana da 3,0)
|ret| > 3σ   2,03 %    (una gaussiana da 0,27 %)
```

Colas gordas: **siete veces más eventos extremos** de los que predice una normal.
Y en una prueba de **carga** eso no es cosmética. Datos gaussianos comprimen mejor
y se reparten más parejo entre particiones, así que harían quedar a Spark mejor de
lo que le corresponde.

### El error que costó dos intentos

La primera versión guardaba 101 cuantiles equiespaciados. El verificador la
rechazó:

```
desviación   0,1239  objetivo 0,0822   SE APARTA
curtosis       50,5  objetivo  24,17   SE APARTA
```

Causa: el primer tramo va del mínimo (−1,33) al percentil 1 (−0,24), y la
interpolación reparte ese 1 % de masa **uniformemente por todo el rango**, cuando
en realidad casi toda está pegada a −0,24. Medido sobre 200.000 muestras:

| rejilla | puntos | desviación | curtosis |
|---|---|---|---|
| 101 uniformes | 101 | +66,5 % | +95,0 % |
| 1000 uniformes | 1001 | +5,8 % | +70,6 % |
| **101 + colas densas** | **111** | **+0,1 %** | **−11,6 %** |

**Once puntos bien colocados baten a novecientos mal colocados**, porque el
problema era resolución en las colas, no resolución global.

Sin el verificador, esos datos habrían llegado a Databricks y la prueba de carga
habría medido otra cosa. Es el mismo patrón que el resto del repositorio: el guard
existe para marcar algo que tiene que marcar, y aquí marcó.

---

## Plan de la prueba de carga, corregido con lo medido

El entorno se midió el 2026-08-26 (notebook 00). Free Edition serverless:

```
Spark 4.1.0 · Python 3.12.3 · Delta 3.4.0 · MLflow 3.8.1
catálogos: samples · system · workspace · preciovivo (creado)
shuffle.partitions = auto      AQE decide, no tú
```

### `SHOW SCHEMAS` sobre un Delta Share devuelve un subconjunto

`samples` está bajo **"Shares received"**: es un Delta Share, no un catálogo
normal. `SHOW SCHEMAS IN samples` devolvió **6 de los 11 esquemas** que la UI
lista, y escribí *"tpcds_sf1 no existe en este workspace"* fiándome de eso.

Era falso. `SHOW TABLES IN <esquema>` sí funciona:

| esquema | lo que hay |
|---|---|
| `tpcds_sf1` | 24 tablas |
| **`tpcds_sf1000`** | **24 tablas · TPC-DS a factor 1000, del orden de 1 TB** |
| `healthverity` | 1 tabla |
| `sec` | 0 tablas |
| `tpch.lineitem` | 29.999.795 filas · 16 col |

**Esto reordena el plan entero, y para mejor.** Hay un terabyte de benchmark
estándar ya montado, con **el mismo esquema a dos escalas**: SF1 y SF1000. Mismas
24 tablas, mismas consultas, mil veces más datos. Es un experimento controlado
servido en bandeja, y nadie puede acusarlo de estar hecho a medida.

El generador sintético no se tira: sigue siendo el banco fino, con la forma del
dominio propio y el tamaño bajo control. Deja de ser el banco pesado.

**El cuello de botella no es lo que yo suponía.** Generar filas es efectivamente
gratis; escribirlas es lo que cuesta:

| operación | medido |
|---|---|
| generar + filtrar + contar 1.000 M filas | 0,8 s |
| **escribir 1 M filas en Delta** | **12,9 s → 77 K filas/s** |

Quince mil veces de diferencia.

### La sonda de generación estaba mal diseñada, y su propio resultado lo delata

```
      1.000.000 filas   0,8 s
  1.000.000.000 filas   0,8 s      <- mil veces más datos, el mismo tiempo
```

Tiempo constante sobre tres órdenes de magnitud significa que se estaba midiendo
la latencia del viaje de ida y vuelta contra el motor, no el trabajo. `range` +
`rand` + `filter` + `count` no lee disco, no escribe y no hace shuffle: es el
mejor caso posible y no informa de nada.

Lo mismo con `lineitem.count()` en 1,3 s para 30 M filas: **Delta guarda el
conteo en su log de transacciones**, así que ese count lee metadatos, no datos.

### Sobre generar 10 GB: ya no hace falta

La estimación de abajo se hizo antes de descubrir `tpcds_sf1000`. Se deja porque
sigue siendo cierta para la pista sintética, y porque explica por qué GENERAR un
terabyte nunca fue viable aquí.

A 77 K filas/s y 7,5 MB por millón de filas:

| objetivo | filas | escritura |
|---|---|---|
| 100 MB | ~13 M | ~3 min |
| 750 MB | ~100 M | ~22 min |
| **10 GB** | **~1.330 M** | **~4,8 horas** |

Casi cinco horas de escritura continua no caben en la cuota diaria. **El techo
realista está en torno a 750 MB**, y decirlo con el número al lado vale más que
haber procesado 10 GB.

Matiz honesto: esos 12,9 s incluyen el arranque en frío del camino de escritura.
Con volúmenes mayores el coste fijo se reparte y el ritmo debería mejorar. Hay que
medirlo, no suponerlo.

---

## Resultados de la pista A · medallón con dato real

Corrido el 2026-08-25 en `preciovivo.{bronze,silver,gold}`, 38.481 filas.

### Spark contra la misma máquina de siempre

| capa | local (SQLite) | Databricks (Spark) | razón |
|---|---|---|---|
| bronze | 0,077 s | 5,4 s | 70× |
| silver | 0,040 s | 6,4 s | 159× |
| gold | 0,058 s | 6,6 s | 114× |
| **total** | **0,175 s** | **18,4 s** | **105×** |

**Spark es 105 veces más lento**, y no porque esté mal usado. A esta escala el
coste fijo —planificar la consulta, hablar por red con el motor, coordinar
tareas— domina por completo al trabajo real. Ese coste se amortiza cuando hay
órdenes de magnitud más datos, y aquí no los hay.

Ése es el número que hace defendible la frase *"es desproporcionado"*. No es una
opinión sobre la herramienta: es la medición de cuándo empieza a compensar.

### Las dos implementaciones coinciden

| | SQLite | Spark |
|---|---|---|
| aceptadas | 38.394 | 38.394 |
| cuarentena | 87 | 87 |
| claves duplicadas | — | **0** |

Dos motores distintos, mismas reglas de silver, mismos conteos exactos. Eso no es
casualidad: valida que la lógica de validación es la misma en los dos sitios.

Las 87 en cuarentena son todas **precio ausente** (0,23 %). No se descartaron en
silencio: están en `silver.cuarentena` con su motivo.

### 3 bytes por fila, y por qué

| tabla | filas | archivos | tamaño | bytes/fila |
|---|---|---|---|---|
| `bronze.gmml` | 38.481 | 1 | 107 KB | **3** |
| `silver.precios` | 38.394 | 1 | 106 KB | **3** |
| `gold.resumen_mensual` | 29 | 1 | 3 KB | 109 |
| `gold.volatilidad_producto` | 73 | 1 | 6 KB | 78 |

El CSV de origen pesa **2,1 MB**; en Delta son **107 KB**. Veinte veces menos, con
diez columnas por fila. La razón está en la cardinalidad:

| columna | valores distintos | repeticiones | qué hace Parquet |
|---|---|---|---|
| `producto` | 147 | 262 | diccionario, ~1 byte/fila |
| `mercado` | 3 | 12.827 | diccionario |
| `unidad` | 21 | 1.832 | diccionario |
| `tendencia` | 5 | 7.696 | diccionario |
| `fecha` | 543 | 71 | diccionario de 2 bytes |
| `precio_kg` | 1.498 | 26 | diccionario de 2 bytes |
| `_origen` | **1** | 38.481 | RLE lo colapsa a casi nada |

Parquet es **columnar**: guarda cada columna junta en vez de fila a fila. Así los
valores vecinos se parecen, y un diccionario más run-length encoding se los come.
En CSV, `"Papa Blanca"` se escribe 262 veces enteras; en Parquet, una vez, y luego
262 referencias de un byte.

Ese contraste es la respuesta a *"¿por qué no basta con un CSV?"*, medida sobre
dato propio.

### Un archivo por tabla, que también es información

A esta escala Spark escribe **un solo archivo** por tabla. No hay problema de
*small files* porque no hay datos suficientes para repartir. Es el mismo mensaje
otra vez: los problemas que Delta resuelve todavía no existen aquí.

### El linaje sale vacío

`system.access.table_lineage` devolvió **0 filas**. Puede tardar en poblarse o
estar restringido en Free Edition. No bloquea nada, y hay que reintentarlo más
tarde antes de afirmar que no funciona.

### Y un hallazgo de negocio, que aquí sí vale

Los diez productos más volátiles del GMML por coeficiente de variación
(desviación entre media, adimensional, así que comparable entre un producto de
S/ 1 y otro de S/ 20):

| producto | días | promedio | coef. variación |
|---|---|---|---|
| Arveja Verde Blanca Serrana | 529 | 4,21 | **0,62** |
| Holantau/Organica | 529 | 9,31 | 0,55 |
| Aji Escabeche | 529 | 3,68 | 0,54 |
| Ajo Criollo O Napuri | 529 | 7,68 | 0,54 |
| Arveja Verde Americana | 529 | 5,49 | 0,54 |

Ninguno es de los de mayor volumen. La papa y la cebolla, que mueven las
toneladas, no aparecen. **Esto es dato real, así que la conclusión se puede
afirmar** — al revés que todo lo que salga de la pista sintética.

---

## Lo que falta medir en la pista B

| | local (SQLite + Python) | Databricks (Spark) |
|---|---|---|
| tiempo de la agregación | 0,175 s a 38 K filas | 18,4 s a 38 K filas |
| a escala TPC-DS SF1 | por medir | por medir |
| a escala TPC-DS SF1000 | **imposible en una máquina** | por medir |

La fila de abajo es el punto entero: existe un volumen donde la columna izquierda
deja de poder responder. Encontrarlo es el experimento.

---

## Lo que hace falta y no puedo hacer yo

Una cuenta de **Databricks Free Edition**, creada por Rodrigo, con la
**verificación por LinkedIn hecha el primer día**: es lo que desbloquea la salida
a internet y algo de GPU.

Datasets reales sin descargar nada: el catálogo `samples` ya viene montado
(NYC taxi, TPC-H). Sirve para la parte de "datos que no me inventé yo".

---

## Fuentes

- [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Serverless compute limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations)
