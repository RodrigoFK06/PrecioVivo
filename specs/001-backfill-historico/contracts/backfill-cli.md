# Contrato · `scripts/backfill.py`

La interfaz que esta feature expone a una persona. Es el único componente nuevo.

## Invocación

```bash
python scripts/backfill.py --desde 2019-11 --hasta 2026-08 [opciones]
```

| opción | por defecto | qué hace |
|---|---|---|
| `--desde AAAA-MM` | *obligatorio* | primer mes a recolectar |
| `--hasta AAAA-MM` | mes en curso | último mes |
| `--db RUTA` | copia de trabajo | base sobre la que escribe |
| `--estado RUTA` | `.backfill-estado.json` | dónde guarda el progreso |
| `--tope-bloqueados N` | `10` (por ciento) | aborta si se supera |
| `--solo-censar` | apagado | resuelve y cuenta, **sin descargar ni escribir** |
| `--reanudar` | apagado | continúa desde el estado en disco |

## Códigos de salida

Un proceso que hace algo mal devuelve algo distinto de cero (Principio II).

| código | significado |
|---|---|
| `0` | terminó y los días bloqueados quedaron por debajo del tope |
| `1` | fallo de la fuente: la navegación no resolvió ninguna fecha |
| `2` | **días bloqueados por encima del tope** — el parser no entiende la época |
| `3` | el presupuesto de embeddings se habría superado; no se gastó nada |
| `4` | la base de destino cambió bajo los pies (conflicto de ETag) |

`2` es el código que importa. Es la diferencia entre *«cargué 1.627 días»* y
*«cargué 900 y me callé los otros 727»*.

## Salida a stdout

Una línea JSON por mes procesado, y un objeto final con el informe completo
(esquema en [data-model.md](../data-model.md)). Nada de barras de progreso: la
salida tiene que poder redirigirse a un archivo y leerse después.

```json
{"mes":"2023-01","en_fuente":21,"cargados":21,"bloqueados":0,"anchos":{"595.3":21}}
{"mes":"2019-11","en_fuente":21,"cargados":0,"bloqueados":21,"anchos":{"612.0":21},
 "motivo_dominante":"geometria-no-reconocida"}
```

## Garantías

1. **Idempotente.** Dos ejecuciones sobre el mismo rango dejan la misma base.
   Verificable comparando el `sha256` del archivo.
2. **Reanudable.** Interrumpirla y relanzarla con `--reanudar` no vuelve a
   descargar lo ya bajado.
3. **No toca la base de producción** hasta que termina. Escribe sobre una copia.
4. **No gasta cuota de embeddings.** El backfill ingesta; indexar es otro paso,
   con su propio tope (`TOPE_EMBEDDINGS`).
5. **Un día bloqueado no detiene el lote**, pero sí queda en el informe con su
   motivo (FR-003).

## Lo que NO hace

- No fusiona productos renombrados. Los reporta (FR-007).
- No reindexa ni republica.
- No corrige días ya cargados: la política es `INSERT OR IGNORE`.
