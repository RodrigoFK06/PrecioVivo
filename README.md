# Precio Vivo

Inteligencia de precios mayoristas del Perú con IA. Toma los reportes diarios del **Gran Mercado Mayorista de Lima (GMML)** publicados por MIDAGRI/EMMSA, los vuelve serie temporal estructurada, y los muestra en un dashboard vivo con resúmenes generados por IA.

Proyecto **build-in-public** de [Árkos](#) — showcase de capacidades data + IA + full-stack.

> **Estado:** Fase 0 (setup + pipeline). Ver [`PLAN.md`](./PLAN.md) para el plan de ejecución completo (v3).

## Fuente y atribución

Datos derivados de reportes públicos de **MIDAGRI – GMML** y mercados mayoristas de Lima.
Precio Vivo republica únicamente **hechos numéricos** (precios, volúmenes) procesados y normalizados; **no** redistribuye los documentos fuente.

**Cifras referenciales, no oficiales.** Fuente original: MIDAGRI / EMMSA.

## Estructura

```
PLAN.md            Plan de ejecución (v3)
pipeline/          Harvester + parser (Python) — descarga, parseo coordenado, carga a Postgres
web/               Dashboard (Next.js) — se agrega en Fase 1(d)
data/              Archivos crudos y muestras (local-only, fuera de git)
```

## Desarrollo

Requiere Python 3.11+ y Node 20+. Setup del pipeline:

```bash
cd pipeline
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -r requirements.txt
```
