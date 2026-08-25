# Investigación · Profundidad histórica del catálogo GMML

**Fecha**: 2026-08-25 · **Feature**: `001-backfill-historico`

Todo número de este documento se midió contra la fuente real durante la
investigación. Lo que es proyección va etiquetado como proyección.

---

## D1 · Cuánta historia hay realmente

**Decisión**: el objetivo es 1.667 días, del 2019-11-04 al 2026-08-24.

**Medición**. Se recorrió la colección 335 entera (12 hojas del índice) y se
contaron fechas distintas resueltas del nombre de cada PDF:

```
páginas de mes GMML   : 82
fechas distintas      : 1667
rango                 : 2019-11-04 -> 2026-08-24
por año  {2019: 40, 2020: 250, 2021: 237, 2022: 241, 2023: 247,
          2024: 242, 2025: 253, 2026: 157}
```

Contra los **529 días de GMML** que hay hoy en la base, son **3,15×**.

**Alternativa descartada**: extrapolar 20 días/mes × 82 meses ≈ 1.640. Habría
dado un número parecido por casualidad y no habría descubierto D2 ni D3.

---

## D2 · La navegación actual no alcanza NADA anterior a 2024

**Decisión**: el navegador necesita reconocer **tres** convenciones de nombre, no
una.

**Medición**. `harvester._PDF_RE` exige `-dd-mm-aaaa.pdf`. Contra la fuente real:

| convención | ejemplo | periodo | apariciones |
|---|---|---|---|
| A | `...-lima-03-01-2024.pdf` | 2024 → hoy | 1.316 |
| B | `...GRAN%20MERCADO%20...%20-%2001/04/22.pdf` | ~2021 → 2023 | 1.620 |
| C | `sisap-ingreso-gmml-04nov19.pdf` | 2019 → 2020 | 425 |

La B lleva **barras dentro del propio nombre de archivo** (`01/04/22`), que es
justo lo que rompe cualquier regex escrita asumiendo que el último segmento de la
ruta no contiene `/`.

Comprobación directa del alcance actual, un trimestre por fila: `url_ok` es
`False` en **los 16 trimestres de 2019 a 2023** y `True` en los 11 de 2024 a
2026. Y sobre la página de noviembre de 2019, `dailies_in_month` devuelve **0
diarios** teniendo 21 PDFs delante.

**Consecuencia para el plan**: sin tocar la navegación, el backfill recolectaría
cero días nuevos y terminaría en verde. Es el fallo silencioso de siempre.

**Riesgo residual medido**: 22 URLs (1,3 %) no llevan fecha legible en el nombre
bajo ninguna de las tres. Se resuelven por contexto HTML o se descartan contadas.

---

## D3 · El parser calla ante ediciones que no entiende

**Decisión**: la compuerta de formato debe validar **la geometría de columnas**,
no solo el encabezado.

**Medición**. Un PDF por año, con el parser real:

| año | filas | con precio | % | warnings | ¿la compuerta lo bloquea? |
|---|---|---|---|---|---|
| 2019 | 72 | 36 | 50,0 | 122 | **no** |
| 2020 | 73 | 72 | 98,6 | 0 | no |
| 2021 | 72 | 72 | 100 | 0 | no |
| 2022 | 72 | 72 | 100 | 0 | no |
| **2023** | **72** | **0** | **0,0** | **144** | **no** |
| 2024 | 72 | 72 | 100 | 0 | no |
| 2025 | 72 | 72 | 100 | 0 | no |
| 2026 | 72 | 72 | 100 | 0 | no |

Una edición de enero de 2023 produce **72 filas y cero precios**, y
`layout_changed` sale `False`. `layout_fingerprint` compara el **orden de los
anclajes del encabezado**, que no cambió: `productos|masa|unidad|precios|equiv|
ultimos`, idéntico en 2019 y en 2026. Lo que cambió está debajo del encabezado.

**Causa raíz, con la fila a la vista.** Centros x de la fila «Acelga»:

```
2023 (página 661,1 pt):  Acelga 54 | : 285 | 2 315 | : 349 | : 400 | Atado 434 …
2026 (página 595,2 pt):  Acelga 48 | 1 255 | : 285 | : 315 | : 360 | Atado 390 …
```

`MASA_MAX = 382` es un umbral en puntos absolutos. En la edición de 2023 la
cuarta columna de masa cae en **400**, pasa de largo, el lector de unidad se
encuentra un `:` en vez de `Atado`, y a partir de ahí la fila entera se
desalinea: sin unidad, sin equivalencia y sin ninguno de los tres precios.

**Verificación de la causa**, escalando los umbrales por `ancho/595,2`:

| año | ancho | k | sin escalar | escalado |
|---|---|---|---|---|
| 2019 | 612,0 | 1,028 | 36/72 · 122 warn | 36/72 · 118 warn |
| 2021 | 612,0 | 1,028 | 72/72 · 0 warn | 72/72 · 0 warn |
| **2023** | **661,1** | **1,111** | **0/72 · 144 warn** | **72/72 · 0 warn** |
| 2026 | 595,2 | 1,000 | 72/72 · 0 warn | 72/72 · 0 warn |

**Rectificación honesta**: la primera pasada de esta prueba dio «hipótesis
refutada» porque escaló `MASA_BANDS` y `HEADER_BAND` pero dejó sin escalar
`NAME_MAX`, `MASA_MIN` y `MASA_MAX` — que son los umbrales que de verdad cortan
las columnas. Casi se descarta la solución correcta por una prueba incompleta. Es
el Principio III leído al revés: un experimento que sale negativo también hay que
verificarlo contra un caso que TIENE que salir positivo.

**El ancho no depende del año.** Se midieron cuatro geometrías —595,2 · 595,3 ·
612,0 · 661,1— **mezcladas dentro del mismo año**: julio de 2023 viene en 612,0 y
octubre de 2023 en 595,2. Así que esto no es «arreglar la época antigua»: es
**día a día**, y también puede pasar mañana.

**Alternativa descartada**: normalizar por un factor fijo por año. Lo desmiente la
propia medición.

**Lo que queda sin resolver: 2019.** El escalado no lo arregla (36/72 con y sin).
Su causa es distinta y se ve en el texto: `':Atado'`, `'43Kilogramo'`, sin espacio
entre la última masa y la unidad. Son 40 días, el **2,4 %** del objetivo. Se
declara fuera de alcance y **contado**, no ignorado.

---

## D4 · La comparación de modelos ya se calcula; lo que falta es medir su promesa

**Decisión**: el criterio de éxito es que el veredicto pase a citar una medición.
No que gane el GBM.

**Medición**, leída del snapshot publicado (`forecastMeta.kill_gate.
comparacion_modelos`):

```
gbm_disponible: True   umbral_gbm: 120
h=1: n_productos=72  n_gbm=72  ganador=ar1
     baseline 0,2074 | ar1 0,1587 | volumen 0,1648 | gbm 0,1795
h=7: n_productos=72  n_gbm=72  ganador=ar1
     baseline 0,5318 | ar1 0,4744 | volumen 0,4980 | gbm 0,4927
```

**Corrección a la especificación**. La primera versión decía que la comparación
era inconclusa por falta de observaciones. Es falso: los productos tienen entre
518 y 529 observaciones contra un umbral de 120, **los 72 se evalúan**. El GBM no
está sin medir — está midiendo y perdiendo, un 13,1 % a 1 día y un 3,9 % a 7.

Lo que de verdad no tiene respaldo es la frase de `forecast.py:885` y `:921-922`:

> «se espera que lo haga con más data»

Es la única afirmación del sistema que ningún cálculo sostiene. El backfill la
resuelve en una dirección o en la otra.

**Corrección a la constitución**. Su Principio IV cita «0,1711 contra 0,1476».
Los valores vigentes son **0,1795 contra 0,1587**. Los datos crecieron desde que
se escribió; se actualiza la cita.

---

## D5 · El pronóstico NO se vuelve más caro con más historia

**Decisión**: el backfill no obliga a rediseñar el paso de forecast.

**Medición** — walk-forward completo, media de tres productos, ambos horizontes:

| n_obs | s/producto | s × 72 en serie |
|---|---|---|
| 150 | 13,22 | 951,8 |
| 250 | 11,17 | 804,2 |
| 375 | 10,28 | 740,5 |
| 529 | 15,19 | 1.093,7 |

Ajuste log-log: `t = 9,07 · n^0,05`. El exponente es **prácticamente cero** y la
serie no es monótona, así que lo honesto es leerlo como *«no se detecta
crecimiento con la longitud en el rango 150–529»*, no como una ley de potencias.

**Mecanismo, que vale más que la curva ajustada**: `MAX_FOLDS = 60` capa los
orígenes de evaluación. Con 529 observaciones o con 1.667 se re-ajusta el GBM el
mismo número de veces; solo crece el tamaño de cada ajuste.

**PROYECCIÓN** (no medición): a 1.667 observaciones, ~13,5 s/producto, dentro de
los 300 s del trabajador. El margen registrado en `docs/aws.md` —39 s de 300—
se mantiene.

**Efecto secundario que sí importa, y no es de coste**: con 60 orígenes fijos
sobre 3,15× de historia, la evaluación se vuelve **más rala en términos
relativos** — mismos pliegues repartidos sobre un tramo tres veces más largo.
Subir `MAX_FOLDS` es una decisión de rigor estadístico, no de rendimiento, y se
deja explícita en el plan en vez de colarse como efecto colateral del backfill.

---

## D6 · Granularidad del índice: mensual para lo histórico

**Decisión**: los `producto-periodo` del tramo histórico se agregan por **mes**;
el tramo reciente sigue por semana.

**Medición del índice publicado hoy** (9.278 chunks, ~1,52 M tokens):

| tipo | chunks | chars/chunk | por día |
|---|---|---|---|
| producto-periodo | 7.991 | 632 | 15,106 |
| evento-anomalia | 617 | 490 | 1,166 |
| mercado-dia | 523 | 1.203 | 0,989 |
| otro-mercado-dia | 75 | 359 | 0,142 |
| producto-perfil | 72 | 1.141 | 0,136 |

**Proyección a 1.667 días**, contra el saldo Jina registrado (5.094.546 tokens):

| granularidad | producto-periodo | total chunks | tokens | % de la cuota |
|---|---|---|---|---|
| semanal | 17.146 | 20.978 | 3,50 M | **68,7 %** |
| **mensual** | **3.943** | **7.775** | **1,41 M** | **27,7 %** |

La semanal **no cabe** bajo el techo del 40 % que fija SC-004. La mensual sí, con
holgura. Y hay un segundo motivo, medido en la iteración anterior: a granularidad
mensual la recuperación es **mejor** (MRR 0,955 contra 0,822) — menos chunks y
más específicos baten a muchos chunks casi idénticos.

**Alternativa descartada**: semanal en todo el rango. Cuesta 2,5× y recupera peor.

**Nota de coste**: los chunks históricos son inmutables, así que se embeben UNA
vez y el caché del indexador los reutiliza. El 27,7 % es un pago único, no
recurrente.

---

## Resumen de decisiones

| # | decisión | qué la fuerza |
|---|---|---|
| D1 | objetivo 1.667 días, 3,15× | censo de las 82 páginas de mes |
| D2 | tres convenciones de URL | 2019-2023 hoy es inalcanzable |
| D3 | compuerta por geometría, no por encabezado | una edición de 2023 da 0 precios en verde |
| D4 | éxito = veredicto medido, no GBM ganador | los 72 productos YA se evalúan |
| D5 | el forecast no se rediseña | `MAX_FOLDS` capa el coste |
| D6 | histórico mensual | semanal cuesta 68,7 % de la cuota y recupera peor |

## Fuera de alcance, declarado

- **Los 40 días de 2019** (2,4 %): causa distinta, sin resolver.
- **Las 22 URLs sin fecha legible** (1,3 %).
- **Fusionar productos que cambiaron de nombre**: se detecta y se reporta; unirlos
  toca la identidad de las series y es otra especificación.
- **Historia de los mercados secundarios**: no existe archivo que recuperar.
