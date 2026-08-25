# Modelo de datos · Profundidad histórica del catálogo GMML

**Feature**: `001-backfill-historico` · **Fecha**: 2026-08-25

El esquema **no cambia**. Lo que cambia es qué se admite dentro de él y qué se
rechaza. Este documento fija esas reglas.

---

## Entidades existentes que el backfill toca

### `precios_diarios` — el día de mercado

```sql
PRIMARY KEY (producto_id, mercado_id, fecha)
```

**La idempotencia de FR-002 ya está en el esquema.** La clave primaria compuesta
hace que reingestar un día no pueda duplicar nada. Lo que el backfill debe elegir
es la política de conflicto, y no es indiferente:

| política | qué hace | veredicto |
|---|---|---|
| `INSERT OR IGNORE` | el primer valor cargado gana | **elegida** |
| `INSERT OR REPLACE` | el último cargado gana | rechazada |

Se elige **IGNORE**. Motivo: la fuente republica ediciones corregidas con el
mismo nombre, y una segunda pasada del backfill no debe poder pisar un día que ya
se validó. Si alguna vez hace falta corregir un día, es una operación explícita y
nombrada, no un efecto colateral de volver a ejecutar el backfill.

**Consecuencia que hay que decir**: un día cargado con el parser roto quedaría
congelado. Por eso T2 (compuerta) va **antes** que T3 (ingesta), y por eso el
backfill escribe sobre una copia hasta que T4 termina.

### `fuentes_raw` — la trazabilidad de la descarga

```sql
UNIQUE (fuente, sha256)
```

Un `sha256` por documento descargado. Sirve de **detector de republicación**: si
la fuente publica dos URLs distintas con el mismo contenido, colisionan aquí.

El campo `fecha` es anulable. Para las 22 URLs sin fecha legible (research.md D2)
se registra la descarga con `fecha = NULL` y **no** se ingesta: quedan contadas y
localizables sin contaminar la serie.

### `productos` — la identidad, que es lo frágil

```sql
nombre_canonico TEXT UNIQUE NOT NULL
```

Aquí vive el riesgo real del backfill. `nombre_canonico` sale del texto del PDF, y
a lo largo de siete años la fuente ha renombrado productos. Dos nombres para la
misma cosa producen **dos series cortas donde debería haber una larga** — que es
exactamente lo contrario de lo que esta feature persigue.

Regla (FR-007): el backfill **detecta y reporta**, no fusiona.

Señal de sospecha, computable sin criterio humano:

- dos productos cuyas series **no se solapan en ninguna fecha**, y
- el final de una está a ≤ 15 días hábiles del inicio de la otra, y
- sus nombres comparten un prefijo normalizado de ≥ 6 caracteres.

Un par que cumple las tres va al informe. Fusionarlos toca la identidad de las
series y es otra especificación.

### `mercados` — la frontera que no se cruza

El backfill escribe **solo** en `GMML`. La colección 335 incluye una página de
diciembre de 2019 titulada *mercado mayorista de productores*, que es otro
mercado: el navegador la descarta por slug.

Principio IV: mercados distintos no se comparan ni se fusionan.

---

## Entidades nuevas, ninguna en la base

Las dos que el backfill necesita viven **fuera** del esquema, porque describen la
ejecución y no el dato.

### Lote de backfill (estado en disco, JSON)

Permite reanudar sin repetir descargas ni gasto.

| campo | tipo | qué es |
|---|---|---|
| `mes` | `AAAA-MM` | la página de mes que se está procesando |
| `fechas_vistas` | lista | fechas resueltas en esa página |
| `cargadas` | lista | días que pasaron la compuerta y entraron |
| `bloqueadas` | mapa fecha → motivo | días rechazados, **con motivo** |
| `sin_fecha` | lista | URLs no resolubles |
| `sha_por_fecha` | mapa | huella del PDF, para no volver a bajarlo |

`bloqueadas` es un mapa y no un contador **a propósito**: un número dice cuántos
se perdieron, un motivo dice si el backfill está roto o si la fuente lo está.

### Informe de backfill (artefacto, JSON)

Lo que FR-003, FR-007 y FR-008 exigen publicar:

| campo | qué responde |
|---|---|
| `dias_en_fuente` | cuántos había |
| `dias_cargados` | cuántos entraron |
| `dias_bloqueados` | mapa motivo → cuenta, y la lista de fechas |
| `geometrias_vistas` | anchos de página encontrados, con su frecuencia |
| `productos_sospechosos` | pares candidatos a renombrado (FR-007) |
| `tokens_embedding` | consumo real contra el proyectado (FR-008) |
| `mae_antes` / `mae_despues` | la comparación de modelos a un lado y al otro |

---

## Reglas de validación, y cuál es nueva

| # | regla | estado |
|---|---|---|
| V1 | El encabezado conserva sus anclajes en orden | **ya existe** (`layout_fingerprint`) |
| V2 | Las columnas caen donde el parser las busca, normalizadas por el ancho de la página | **NUEVA — T2** |
| V3 | Una fila sin unidad o sin precio es una fila fallida, no una fila con nulos | **NUEVA — T2** |
| V4 | Un día con > 20 % de filas fallidas se bloquea entero | **NUEVA — T2** |
| V5 | `0,2 ≤ precio_hoy_kg ≤ 60` | ya existe, como aviso |
| V6 | La fecha del PDF coincide con la deducida de su URL | **NUEVA — T1** |

**V2 y V4 son el corazón de la feature.** Sin ellas, la edición de enero de 2023
—72 filas, cero precios— entra en la base y la ejecución termina en verde.

V6 es barata y cierra un agujero que las tres convenciones abren: si una URL dice
`01/04/22` y el PDF dice otra cosa, alguien se equivocó y hay que verlo.

### Sobre el umbral del 20 % de V4

Es el único número de este documento que **no** está medido: se elige.

Justificación de por qué no es arbitrario en la práctica: sobre las ediciones
sanas la tasa de fallo medida es **0 %** (0 warnings en 2020-07 a 2026-07), y
sobre las rotas es **100 %** (2023) o **50 %** (2019). No hay nada cerca del 20 %,
así que cualquier umbral entre el 5 % y el 45 % separa los mismos casos. Se deja
declarado como elección y se revisa si aparece una edición intermedia.
