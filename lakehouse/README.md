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

## Plan de la prueba de carga

**No hay objetivo de 10 GB.** El objetivo es la curva, y se sube escalón a escalón
midiendo en cada uno:

```
10 MB → 100 MB → 1 GB → 10 GB
```

Se para donde muerda la cuota. Free Edition **no publica límites numéricos**: usa
una política de uso justo, y al pasarte **apaga el workspace el resto del día**, y
en casos extremos el resto del mes. Así que "se rompió en 1 GB" es un resultado
igual de publicable que "llegó a 10 GB".

En cada escalón se mide lo mismo en los dos lados:

| | local (SQLite + Python) | Databricks (Spark) |
|---|---|---|
| tiempo de la agregación | | |
| memoria máxima | | |
| tamaño en disco | | |

Sin la columna de la izquierda, "Spark tardó 30 segundos" no significa nada.

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
