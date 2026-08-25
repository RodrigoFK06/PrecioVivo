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

`retrieval_gold.json` — **62 casos en 16 categorías**, repartidos **43 fáciles
(69,4 %) / 19 adversariales (30,6 %)**.

El tope del 70 % de fáciles **lo fija una prueba**, no la buena voluntad
(`tests/test_evals_goldset.py`). Un gold set crece por donde es cómodo crecer, y
lo cómodo es añadir preguntas naturales: cada producto nuevo del catálogo sugiere
tres. Los adversariales hay que pensarlos. Sin un tope que falle, la proporción
se erosiona sola y el recall sube sin que el sistema haya mejorado — la métrica
se vuelve más fácil, no el producto mejor.

| dificultad | qué es |
|---|---|
| `facil` | pregunta natural, con respuesta directa en el corpus |
| `adversarial` | construido para que el sistema falle de una forma concreta: premisa falsa, producto inexistente, colisión de nombres, cruce de mercados, instrucción hostil, fuera de rango, o la regresión de un fallo real |

Familias adversariales, todas con al menos un caso y ninguna con más de la mitad
del cupo (ambas cosas son pruebas): premisa-falsa, fuera-de-rango, inyección,
cruce-de-mercados, sin-respuesta, comparación, otro-mercado, unidad, pronóstico.

**Los predicados son granularity-independent.** Un caso pide "un chunk con
`slug=brocoli` que cubra el 2025-02-19", no un id literal. Por eso el mismo gold
set evalúa día, semana y mes sin reescribirse: los ids cambian, el hecho no.

```json
{
  "id": "brocoli-premisa-falsa-bajada",
  "pregunta": "¿por qué bajó tanto el brócoli en febrero de 2025?",
  "dificultad": "adversarial",
  "debe_recuperar": [
    {"slug": "brocoli", "tipo": "producto-periodo", "cubre_fecha": "2025-02-19"}
  ],
  "debe_afirmar": ["241"],
  "nota": "La premisa es falsa: subió +241,6%."
}
```

Predicados de RECUPERACIÓN: `id`, `slug`, `tipo`, `cubre_fecha` (solapamiento del
rango del chunk), `texto_contiene`, y `no_debe_recuperar` (falso positivo).
Predicados de GENERACIÓN, que consume `run_generacion.py`: `debe_abstenerse`,
`debe_afirmar` (basta una subcadena), `no_debe_afirmar` (ninguna).

### Cómo leer el recall, que es lo que más se malinterpreta

`recall@k` sale **1,000** y eso NO significa que la búsqueda sea perfecta.
Significa que el **piso determinista** hace casi todo el trabajo. Medido el
2026-08-25 bajando k:

| k | recall@k | solo lo recuperado |
|---|---|---|
| 1 | 0,983 | 0,299 |
| 2 | 0,991 | 0,385 |
| 3 | 0,991 | 0,425 |
| 5 | 0,991 | 0,675 |
| 8 | **1,000** | **0,750** |

El piso son ocho chunks que entran pase lo que pase, así que el recall está
saturado por construcción. La cifra que mide la búsqueda vectorial es **`solo lo
recuperado`**, y el MRR (0,818). Publicar el 1,000 a secas sería un número
honesto usado de forma engañosa.

### Las fechas se anclan en el tramo estable

El índice se publica en dos partes con vidas muy distintas:

| parte | cubre | se rehace |
|---|---|---|
| `rag-historico` | 2024-07-01 → 2026-08-07 | de tarde en tarde: reindexar cuesta cuota |
| `rag-reciente` | **un solo día** | cada mañana, con la corrida de la tubería |

Un predicado con `cubre_fecha` posterior al fin del histórico **caduca solo**.
Pasó: seis casos se anclaron en el último día publicado y murieron en cinco días,
cuando la tubería avanzó. Hay una prueba que lo impide antes de que ocurra, no
después.

Los predicados **sin** `cubre_fecha` quedan fuera de esa regla a propósito: los
del MMF2 y los perfiles solo viven en el tramo reciente y no pueden anclar fecha.
Su validez la cubre la prueba de alcanzabilidad.

### Los hechos son reales

Cada cifra citada en `nota` se midió sobre `web/data/snapshot.json` o sobre el
índice publicado. Ninguna es inventada. Dos pruebas lo sostienen: todo predicado
positivo tiene que ser **alcanzable** contra el índice publicado, y todo tipo de
chunk del corpus tiene que tener **al menos un predicado que lo exija**.

### Casos sin predicado positivo

Cuatro casos no tienen nada que recuperar y existen para verificar que el sistema
no fabrica una coincidencia: `producto-inexistente-salmon`,
`inyeccion-olvida-el-catalogo`, `papa-fuera-de-rango-2018` y
`domingo-sin-publicacion`. Todos declaran `debe_abstenerse` o `debe_afirmar`: un
caso sin nada que comprobar no falla nunca, y hay una prueba que lo impide.

### El caso que caducó

`papaya-no-es-papa` nació exigiendo abstención — el GMML no vendía papaya — y
**caducó en silencio** cuando el boletín 338 metió `PAPAYA SELVA` en el MMF2.
Durante ese tiempo la evaluación premiaba la respuesta equivocada. Hoy es un caso
de `cruce-de-mercados` y hay un guard que compara el gold set contra el catálogo
publicado en las dos direcciones, para que no vuelva a pasar.

## Lo que esto NO mide todavía

**58 de 62 casos** tienen predicado de recuperación y se miden sin coste, sin red
y sin claves. Solo **14** tienen predicado de generación, y esos exigen el modelo
real (`run_generacion.py`, con `AI_API_KEY`).

Ahí está el hueco que importa: **14 de los 19 adversariales viven en la capa de
generación**, así que la mayor parte del valor adversarial de este gold set no se
mide en cada corrida. La recuperación puede traer el chunk que contradice una
premisa falsa y el modelo tragársela igual — y `run_retrieval.py` daría 1,000.

Tampoco se mide si la respuesta razona bien ni si atribuye causas que el dato no
respalda. `run_generacion.py` mide invención de CIFRAS y contenido obligado o
prohibido por subcadena: determinista y estrecho, a propósito.
