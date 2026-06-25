# Precio Vivo

Inteligencia de precios mayoristas del Perú con IA. Toma los reportes diarios del **Gran Mercado Mayorista de Lima (GMML)** publicados por MIDAGRI/EMMSA, los vuelve serie temporal estructurada, y los muestra en un dashboard vivo con resúmenes generados por IA.

Proyecto **build-in-public** de [Árkos](https://www.xn--rkos-4na.com/) — showcase de capacidades data + IA + full-stack.

> **En vivo:** **[precio-vivo.vercel.app](https://precio-vivo.vercel.app)** — datos del GMML actualizados cada día hábil, con resumen y pronóstico de precios por IA.

## CLI — `precio`

Los mismos datos, en tu terminal: precios del día, tendencia con _sparkline_, pronóstico con IA y consulta en lenguaje natural.

```bash
cd pipeline && pip install -e .

precio                                          # panorama del día (resumen IA + movers)
precio ver zanahoria                            # precio, tendencia, sparkline, pronóstico
precio pregunta "¿qué está más barato hoy?"     # consulta en lenguaje natural (IA)
precio resumen                                  # el resumen del día redactado por IA
precio alertas                                  # variaciones fuertes y anomalías
precio tabla --buscar papa                      # tabla filtrable
```

La consulta NL usa un proveedor OpenAI-compatible (DeepSeek por defecto): exporta `AI_API_KEY` (ver [`web/.env.example`](./web/.env.example)). Todo lo demás funciona sin clave, leyendo el snapshot local o `https://precio-vivo.vercel.app/snapshot.json`.

## Fuente y atribución

Datos derivados de reportes públicos de **MIDAGRI – GMML** y mercados mayoristas de Lima.
Precio Vivo republica únicamente **hechos numéricos** (precios, volúmenes) procesados y normalizados; **no** redistribuye los documentos fuente.

**Cifras referenciales, no oficiales.** Fuente original: MIDAGRI / EMMSA.

## Estructura

```
pipeline/          Pipeline + CLI (Python): harvester, parser coordenado, forecast (GBM),
                   capa IA (DeepSeek) y la CLI `precio`
web/               Dashboard (Next.js 16) + API de consulta en lenguaje natural
data/              Archivos crudos y dev DB (local-only, fuera de git)
PLAN.md            Plan de ejecución (v3)
```

## Desarrollo

Requiere Python 3.11+ y Node 20+. Setup del pipeline:

```bash
cd pipeline
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -r requirements.txt
```
