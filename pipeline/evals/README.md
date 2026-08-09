# Evaluación de la capa IA

## Por qué existe

Sin esto, "RAG real" es una afirmación que nadie puede falsear. La evaluación
mide si la recuperación trae los hechos correctos y **falla con código 1** cuando
el recall cae del umbral, así que puede colgarse de CI (bloque 6).

También es lo que sostiene la decisión de granularidad del README: la tabla
día/semana/mes sale de correr esto, no de una intuición. De hecho la primera
corrida **refutó** la hipótesis original —el grano mensual recupera mejor, no
peor— y eso obligó a agregar una métrica que midiera lo que de verdad se
afirmaba. Está documentado en el README.

## Uso

```bash
cd pipeline

python evals/run_retrieval.py                        # granularidad semana
python evals/run_retrieval.py --granularidad todas   # la tabla del README
python evals/run_retrieval.py --embedder local -v    # calidad semántica real
python evals/run_retrieval.py --umbral 0.9           # sale 1 si baja de ahí
python evals/run_retrieval.py --json                 # para CI
```

`--embedder` por defecto es `fake`: determinista, sin red ni claves, mide la
MECÁNICA (filtro estructurado, piso determinista, fusión RRF). Para medir calidad
semántica hace falta `local` (model2vec) o `api`. **Los números de embebedores
distintos no son comparables entre sí**, y el reporte siempre imprime cuál usó.

## Métricas

| Métrica | Qué mide |
|---|---|
| `recall@k` | Fracción de predicados del gold satisfechos en el top-k del contexto completo (piso + recuperados). Es lo que de verdad llega al modelo. |
| `solo lo recuperado` | Igual, pero sin el piso determinista: aísla la calidad de la búsqueda. |
| `solo el piso` | Igual, pero sin la búsqueda: mide cuánto cubre la garantía. |
| `MRR` | Inverso de la posición del primer chunk relevante. |
| `span de la evidencia` | Días que cubre el chunk recuperado. **Menos es mejor.** |
| `violaciones` | Chunks que el gold marca como `no_debe_recuperar`. Cualquiera > 0 falla. |

`recall` y `MRR` miden si el chunk correcto **se encuentra**. `span` mide si
**sirve para responder**: un chunk mensual satisface el predicado igual que uno
semanal, pero le entrega al modelo ~20 días hábiles promediados en vez de ~5, y
el pico que la pregunta busca queda diluido. Sin esta métrica, la evaluación
premiaría el grano equivocado.

## El gold set

`retrieval_gold.json` — 32 casos en 8 categorías: producto+tiempo, anomalía,
estacional, agregada, ficha, agrupación, desambiguación y sin-respuesta.

**Los predicados son granularity-independent.** Un caso pide "un chunk con
`slug=brocoli` que cubra el 2025-02-19", no un id literal. Por eso el mismo gold
set evalúa día, semana y mes sin reescribirse: los ids cambian, el hecho no.

```json
{
  "id": "brocoli-alza-feb-2025",
  "pregunta": "¿por qué subió tanto el brócoli en febrero de 2025?",
  "debe_recuperar": [
    {"slug": "brocoli", "tipo": "producto-periodo", "cubre_fecha": "2025-02-19"}
  ],
  "nota": "Alza real de +241,6% en la semana 2025-W08."
}
```

Predicados disponibles: `id`, `slug`, `tipo`, `cubre_fecha` (solapamiento del
rango del chunk), `texto_contiene`.

### Los hechos son reales

Cada cifra citada en `nota` se midió sobre `web/data/snapshot.json`. Ninguna es
inventada. Para reproducirlas:

```bash
python -c "
from preciovivo.corpus import cargar_snapshot, anomalias_historicas
snap = cargar_snapshot('../web/data/snapshot.json')
an = sorted(anomalias_historicas(snap), key=lambda a: -abs(a['z']))
for a in an[:10]: print(a['nombre'], a['tipo'], a['fecha'], a['z'])
"
```

### Casos sin respuesta

Dos casos (`producto-inexistente-salmon`, `pollo-no-esta-en-gmml`) no tienen
predicado positivo: no hay nada correcto que recuperar. Existen para verificar
que el sistema no rompe y que no fabrica una coincidencia. `papaya-no-es-papa`
solo mide el **falso positivo**: si "papaya" arrastrara las papas por prefijo, la
respuesta mezclaría dos productos distintos.

## Lo que esto NO mide todavía

Solo **recuperación**. No mide la calidad de la respuesta que el modelo escribe
sobre ese contexto — si cita bien las cifras, si respeta la regla de no atribuir
causas, si dice "no sé" cuando corresponde. Eso es el bloque 4, y este harness es
su base.
