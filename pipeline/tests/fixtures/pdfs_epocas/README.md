# Cuatro ediciones, cuatro geometrías

Fixtures de `001-backfill-historico`. No son ejemplos: cada uno existe para que
un guard tenga algo concreto que marcar (Constitución, Principio III).

**Los PDF no se versionan.** `.gitignore:28` lo prohíbe —«LEGAL §0.5: never
re-host source PDFs/prose; keep local-only»— y esa regla vale más que la
comodidad de tener el fixture a mano. Los tests siguen el patrón ya establecido
en `test_parser*.py`: si el archivo no está, `pytest.skip`.

Se obtienen con `pipeline/tests/fixtures/pdfs_epocas/traer.py`, que los baja a
este directorio desde el CDN de gob.pe.

| archivo | ancho | qué hace el parser HOY | qué debe hacer tras T2 |
|---|---|---|---|
| `2019-11-04_612pt.pdf` | 612,0 | 36/72 precios, 122 warnings, **en verde** | bloquear y contar |
| `2021-01-04_612pt.pdf` | 612,0 | 72/72, 0 warnings | idéntico (ancho ≠ 595 que ya iba bien) |
| `2023-01-03_661pt.pdf` | 661,1 | **0/72 precios, 144 warnings, en verde** | 72/72, 0 warnings |
| `2026-01-05_595pt.pdf` | 595,2 | 72/72, 0 warnings | idéntico |

El de 2021 es el que evita el falso arreglo: viene en 612 pt —no en los 595 de
referencia— y aun así parsea perfecto hoy. Si la normalización por ancho lo
rompiera, estaría corrigiendo el síntoma equivocado.

Medido el 2026-08-25. Reproducible con
`specs/001-backfill-historico/quickstart.md`, paso 0b.
