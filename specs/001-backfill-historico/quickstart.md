# Quickstart · verificar la profundidad histórica

**Feature**: `001-backfill-historico`

Cómo comprobar que esto funciona — y, antes, cómo reproducir que **hoy no**.

Contratos: [backfill-cli.md](./contracts/backfill-cli.md) ·
[parser-compuerta.md](./contracts/parser-compuerta.md). Entidades y reglas:
[data-model.md](./data-model.md).

## Prerrequisitos

```bash
cd pipeline
./.venv/Scripts/python.exe -c "import pdfplumber, numpy, sklearn; print('ok')"
```

Los cuatro PDF que fijan la compuerta **no están en el repositorio**:
`.gitignore:28` lo prohíbe por la regla legal §0.5 —no re-hospedar documentos de
la fuente—. Se traen, y el script comprueba la huella, no solo el 200:

```bash
./.venv/Scripts/python.exe tests/fixtures/pdfs_epocas/traer.py
```

Sin credenciales de AWS y sin clave de Jina: los pasos 0 a 3 leen de `gob.pe` y
escriben en local. El gasto es 0 USD en todos ellos.

---

## Paso 0 · Reproducir el fallo, antes de arreglarlo

Esto debe fallar. Si no falla, la premisa de la feature es falsa y hay que
reabrir [research.md](./research.md).

**0a. La navegación no ve 2019-2023:**

```bash
./.venv/Scripts/python.exe -c "
from preciovivo import harvester as h
p=[u for u in h.month_pages(max_sheets=12) if 'noviembre-2019' in u][0]
print('diarios que encuentra:', len(h.dailies_in_month(p)))"
```

Esperado **hoy**: `0`. Esperado **después de T1**: `21`.

**0b. El parser aprueba una edición que da cero precios:**

```bash
./.venv/Scripts/python.exe -c "
from preciovivo.parser import parse_report
r = parse_report('tests/fixtures/pdfs_epocas/2023-01-03_661pt.pdf')
print('filas', len(r.rows),
      '| con precio', sum(1 for x in r.rows if x.precio_hoy_kg is not None),
      '| layout_changed', r.layout_changed)"
```

Esperado **hoy**: `filas 72 | con precio 0 | layout_changed False`.
Esperado **después de T2**: `filas 72 | con precio 72 | layout_changed False`.

---

## Paso 1 · Censar sin descargar ni escribir

```bash
python scripts/backfill.py --desde 2019-11 --hasta 2026-08 --solo-censar
```

Criterio: **1.667 fechas** entre `2019-11-04` y hoy, y las tres convenciones de
nombre representadas. Cero descargas, cero escrituras.

## Paso 2 · La compuerta, contra fallos conocidos

```bash
cd pipeline && ./.venv/Scripts/python.exe -m pytest tests/test_parser_geometria.py -v
```

Las cuatro pruebas del contrato deben pasar, **incluida la de 2019 que exige
`layout_changed == True`**. Un guard que solo aprueba no es un guard.

## Paso 3 · Backfill sobre una copia

```bash
cp data/preciovivo.db data/preciovivo.backfill.db
python scripts/backfill.py --desde 2019-11 --hasta 2026-08 \
    --db data/preciovivo.backfill.db --tope-bloqueados 10
echo "codigo de salida: $?"
```

Criterios:

- código de salida **0** (un `2` significa que se bloquearon más días de la
  cuenta: eso es información, no un fracaso — hay que leer los motivos);
- ≥ **1.500 días** distintos en `precios_diarios` para `GMML` (SC-001);
- los días bloqueados aparecen **con motivo**, no como un número (FR-003).

**Idempotencia** (FR-002) — la comprobación que no se puede saltar:

```bash
sha256sum data/preciovivo.backfill.db > /tmp/antes
python scripts/backfill.py --desde 2019-11 --hasta 2026-08 --db data/preciovivo.backfill.db
sha256sum -c /tmp/antes
```

## Paso 4 · Índice, con tope antes de gastar

```bash
python -m preciovivo.indexer --db data/preciovivo.backfill.db --dry-run
```

Criterio: el consumo proyectado queda **por debajo del 40 %** de la cuota Jina
(SC-004). La proyección de [research.md](./research.md) D6 es 27,7 % a
granularidad mensual. Si sale por encima, **no se ejecuta**: se revisa la
granularidad.

## Paso 5 · El veredicto, que es el objetivo

```bash
./.venv/Scripts/python.exe -c "
from preciovivo.forecast import forecast_all
import json
c = forecast_all('../data/preciovivo.backfill.db')['kill_gate']['comparacion_modelos']
print(json.dumps(c['por_horizonte'], ensure_ascii=False, indent=1))
print(c['veredicto_global'])"
```

Referencia de hoy, para comparar contra ella:

| horizonte | baseline | ar1 | volumen | gbm | gana |
|---|---|---|---|---|---|
| 1 día | 0,2074 | **0,1587** | 0,1648 | 0,1795 | ar1 |
| 7 días | 0,5318 | **0,4744** | 0,4980 | 0,4927 | ar1 |

Criterios (SC-002, SC-003):

- el veredicto cita el error de las cuatro familias, sobre cuántos productos y
  con cuánta historia;
- **la frase «se espera que gane con más historia» ya no aparece**, gane quien
  gane.

Si el GBM sigue perdiendo con 3,15× de historia, **eso es el resultado** y se
publica. La feature entrega una medición, no un ganador.

## Paso 6 · Que nada de lo que ya funcionaba se rompió

```bash
cd pipeline && ./.venv/Scripts/python.exe -m pytest -q     # 459 + las nuevas
cd ../web && npm test                                       # 218
python -m preciovivo.evals.run_recuperacion                 # recall >= 0,9
python -m preciovivo.evals.run_generacion                   # invención de cifras = 0,0
```

El umbral de invención es **0,0 sin margen**: la promesa del prompt es «nunca
inventes cifras», y una promesa con tolerancia no es una promesa.

## Cómo deshacerlo

El backfill escribe sobre `data/preciovivo.backfill.db`. Para descartarlo, se
borra ese archivo: la base de producción no se toca hasta el paso de
publicación, y esa escritura va con ETag condicional.
