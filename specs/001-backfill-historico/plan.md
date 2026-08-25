# Implementation Plan: Profundidad histórica del catálogo GMML

**Branch**: `001-backfill-historico` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-backfill-historico/spec.md`

## Summary

Llevar el catálogo de **529 a ~1.627 días** de mercado (2019-11-04 → hoy) sin
gastar un dólar, y usar esa historia para resolver la única afirmación del
sistema que hoy no tiene respaldo: *«se espera que el GBM gane con más data»*.

La investigación (ver [research.md](./research.md)) cambió el trabajo de sitio.
No es «bajar más PDFs»: es que **dos capas mienten en silencio** sobre la
historia antigua, y hay que arreglarlas antes de ingerir nada.

1. **La navegación no alcanza 2019-2023.** Tres convenciones de nombre de
   archivo, y `_PDF_RE` solo conoce la de 2024+. Hoy `dailies_in_month` devuelve
   0 diarios sobre una página con 21 PDFs.
2. **El parser produce basura en verde.** Una edición de enero de 2023 da 72
   filas y **cero precios**, y la compuerta de formato la aprueba: valida el
   orden del encabezado, que no cambió, mientras la geometría de columnas sí.
   Causa raíz: umbrales en puntos absolutos contra páginas de 595 a 661 pt.

El orden es, por tanto: **primero los guards, luego la ingesta**. Un backfill
sobre el parser actual cargaría días vacíos y terminaría en SUCCEEDED.

## Technical Context

**Language/Version**: Python 3.13 (Lambda) / 3.14 (local). Sitio en TypeScript ·
Next.js sobre Vercel.

**Primary Dependencies**: `pdfplumber` 0.11.10 y `requests` 2.34.2 (ingesta),
`numpy` 2.5.0 · `scikit-learn` 1.9.0 · `holidays` 0.99 (forecast), `openai`
2.43.0 hablando con Jina (índice). Sin dependencias nuevas.

**Storage**: SQLite (`data/preciovivo.db`) como base canónica, replicada en S3 con
bloqueo optimista por ETag. Snapshot JSON e índice RAG cuantizado a int8 como
artefactos publicados.

**Testing**: `pytest` — 459 pruebas de pipeline, 218 del sitio, cuatro trabajos
de CI independientes.

**Target Platform**: el backfill corre **en local, una vez**; la operación diaria
sigue en Lambda + Step Functions.

**Project Type**: pipeline de datos + sitio estático + infraestructura CDK.

**Performance Goals**: el backfill completo por debajo de una jornada de trabajo
sin vigilancia; la operación diaria sin cambio de latencia. Ambos son objetivos
de comodidad, no requisitos: nadie espera delante de la pantalla.

**Constraints**:
- **Gasto 0 USD.** No negociable.
- **Cuota Jina**: 5.094.546 tokens, techo autoimpuesto del 40 % (SC-004). La
  granularidad mensual proyecta 27,7 %; la semanal 68,7 % y queda descartada.
- **Trabajador de forecast**: 300 s. Medido: no crece con la historia (D5).
- **Estado de Step Functions**: 36,6 KB de 256 KB.
- **La operación diaria no se interrumpe** (FR-005, SC-005).

**Scale/Scope**: 1.667 días en la fuente, ~1.627 alcanzables tras descartar los
40 de 2019. 72 productos con historia larga, 147 en el catálogo. ~7.775 chunks
proyectados contra 9.278 de hoy.

## Constitution Check

*GATE: debe pasar antes de la Fase 0. Re-evaluado tras la Fase 1.*

| Principio | Cómo lo cumple este plan | Antes | Después |
|---|---|---|---|
| **I · Nada se afirma sin calcularse** | Los seis números que gobiernan el plan —1.667 días, tres convenciones, 0/72 precios en 2023, 0,1795 vs 0,1587, exponente 0,05, 27,7 % de cuota— se midieron contra la fuente real, no se estimaron. Dos afirmaciones de la especificación se corrigieron al medirlas. | PASA | PASA |
| **II · Un fallo que no deja rastro no existe** | El corazón del plan. La compuerta actual aprueba una edición que da cero precios: es exactamente el fallo silencioso del postmortem, en una capa que aún no se había auditado. T1 y T2 lo convierten en un `Fail` visible **antes** de que se ingeste nada. | **NO PASA hoy** | PASA |
| **III · Todo guard se prueba contra un fallo conocido** | La compuerta nueva se prueba contra el PDF de 2023 que hoy pasa en verde, y contra el de 2019 que degrada al 50 %. Ambos archivos quedan como fixtures. La prueba de escala falló en su primera pasada por incompleta; la corrección está registrada en research.md D3. | PASA | PASA |
| **IV · La honestidad del modelo sobre su lucimiento** | El criterio de éxito es que el veredicto pase a citar una medición, **no** que el GBM gane. Si sigue perdiendo con 3,15× de historia, se publica y se retira la promesa. El anti-leakage no se toca: la salida se compara byte a byte antes y después. | PASA | PASA |
| **V · Permisos mínimos y coste conocido** | Sin recursos nuevos de AWS: el backfill corre en local contra la base y sube el resultado por el camino que ya existe. Coste en dinero 0; coste en cuota Jina 27,7 %, proyectado y con un tope que aborta antes de gastar. | PASA | PASA |

**Veredicto**: el Principio II **está incumplido hoy** en la ruta de ingesta
histórica. No es un motivo para detener el plan — es su justificación. El plan no
puede empezar por la ingesta.

## Project Structure

### Documentation (this feature)

```text
specs/001-backfill-historico/
├── spec.md              # qué y por qué
├── plan.md              # este archivo
├── research.md          # Fase 0: seis decisiones medidas
├── data-model.md        # Fase 1: entidades y reglas de validación
├── quickstart.md        # Fase 1: cómo ejecutarlo y verificarlo
├── contracts/           # Fase 1: contratos de CLI y de artefactos
└── tasks.md             # Fase 2 — la crea /speckit-tasks, no este comando
```

### Source Code (repository root)

```text
pipeline/preciovivo/
├── harvester.py        # (T1) tres convenciones de URL + navegación por rango
├── parser.py           # (T2) geometría normalizada + compuerta que la vigila
├── ingest.py           # (T3) modo backfill idempotente por lote
├── corpus.py           # (T5) granularidad mensual para el tramo histórico
├── forecast.py         # (T6) veredicto con historia declarada
└── store.py            #      sin cambios: el UNIQUE por (producto, fecha) ya da idempotencia

pipeline/tests/
├── test_harvester_epocas.py    # (T1) las tres convenciones, con URLs reales
├── test_parser_geometria.py    # (T2) 2023 debe dar 72/72; 2019 debe BLOQUEARSE
├── test_backfill_idempotente.py# (T3) dos pasadas, misma base
└── fixtures/pdfs_epocas/       # los PDFs de 2019, 2021, 2023 y 2026

scripts/
└── backfill.py         # (T4) el ejecutor: por lotes, reanudable, con presupuesto

web/data/               # artefactos publicados; los reescribe T5/T7
docs/aws.md             # (T8) sin recursos nuevos, pero el techo de cuota cambia
```

**Structure Decision**: se mantiene la estructura actual. El backfill **no** es un
componente nuevo: son cambios quirúrgicos en cuatro módulos del paquete
`preciovivo` más un script de ejecución. Introducir un subproyecto para algo que
se ejecuta una vez sería la clase de complejidad que el Principio V rechaza.

## Fases de ejecución

El orden no es negociable: **guards → ingesta → índice → veredicto**.

| # | fase | entrega | puerta para pasar a la siguiente |
|---|---|---|---|
| T1 | Navegación | `harvester` resuelve las tres convenciones | las 82 páginas dan 1.667 fechas |
| T2 | Compuerta | `parser` normaliza geometría y bloquea lo que no entiende | 2023 da 72/72; 2019 sale **bloqueado y contado** |
| T3 | Ingesta | modo backfill idempotente por lote | dos pasadas dejan la misma base |
| T4 | Ejecución | ~1.627 días cargados | días bloqueados < 10 %, todos nombrados |
| T5 | Índice | corpus mensual en el tramo histórico | consumo real ≤ 40 % de la cuota |
| T6 | Veredicto | comparación recalculada, con historia declarada | la frase «se espera que gane» ya no está |
| T7 | Publicación | snapshot e índice al aire | evals de recuperación y generación en umbral |
| T8 | Registro | `docs/aws.md` y `estado-tecnico.md` al día | los techos medidos reflejan lo nuevo |

La operación diaria sigue corriendo durante T1-T8. El backfill escribe sobre una
copia de la base y solo la sustituye al final de T4, con la misma escritura
condicional por ETag que usa la tubería.

## Complexity Tracking

Sin violaciones que justificar. El plan no añade servicios, ni dependencias, ni
recursos de AWS, ni un subproyecto.

Una decisión se registra aquí por ser deliberada y no obvia:

| Decisión | Por qué | Alternativa rechazada |
|---|---|---|
| El backfill corre **en local**, no en Lambda | Es una ejecución única de horas contra una fuente lenta. Meterlo en Step Functions costaría un `Map` nuevo, un rol nuevo y un estado nuevo para algo que no se repetirá. | Un `Distributed Map`: correcto para lo recurrente, ceremonia para lo que pasa una vez. |
| `MAX_FOLDS` **no** se toca en esta feature | Subirlo cambia el rigor de la evaluación, y hacerlo en la misma corrida que triplica la historia mezcla dos causas: no se sabría cuál movió el MAE. | Subirlo «ya que estamos». |
