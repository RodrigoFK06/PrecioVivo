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
precio pregunta "¿por qué subió la papa?"       # consulta en lenguaje natural (IA + RAG)
precio contexto "¿por qué subió la papa?"       # QUÉ recupera el RAG, sin llamar al modelo
precio resumen                                  # el resumen del día redactado por IA
precio alertas                                  # variaciones fuertes y anomalías
precio tabla --buscar papa                      # tabla filtrable
```

La consulta NL usa un proveedor OpenAI-compatible (DeepSeek por defecto): exporta `AI_API_KEY` (ver [`web/.env.example`](./web/.env.example)). Todo lo demás funciona sin clave, leyendo el snapshot local o `https://precio-vivo.vercel.app/snapshot.json`.

`precio contexto` es la ventana al RAG: muestra qué chunks se recuperaron, cuáles están garantizados y con qué score — útil para entender una respuesta o depurar una mala.

## Consulta en lenguaje natural: RAG sobre el dataset

### El problema que resuelve

La consulta anterior le mandaba al modelo **el catálogo entero**: una línea por producto con el precio de hoy, la variación contra ayer y la tendencia. Con eso se responde "¿qué está más barato?", pero no *"¿por qué subió la papa esta semana?"* — la evidencia temporal sencillamente no estaba en el prompt, y ningún prompt lo arregla.

Con 72 productos, recuperar *el producto* por embeddings sería decorativo: un substring sin acentos ya lo resuelve. **El RAG acá no es para encontrar el producto, es para ensamblar la evidencia temporal.** Eso es lo que define todo el diseño.

### El corpus se genera, no se recolecta

No hay corpus documental que indexar. `categoria` es NULL en los 73 del catálogo, no hay descripciones, y el único texto nativo es el nombre canónico. Además, [PLAN.md §0.5](./PLAN.md) prohíbe almacenar la prosa de la fuente: solo republicamos hechos numéricos.

Así que el corpus se **sintetiza desde nuestros propios hechos**. La restricción legal y la de datos empujan en la misma dirección, y el resultado es mejor que raspar texto ajeno: cada chunk dice exactamente lo que el dato respalda, ni más.

Cuatro tipos, sobre `web/data/snapshot.json` (función pura de un artefacto ya versionado, así lo que el RAG recupera es exactamente lo que el dashboard muestra):

| Tipo | Grano | Cantidad | Contesta |
|---|---|---|---|
| `producto-periodo` | producto × semana ISO | 7 991 | "¿por qué subió esta semana?" |
| `evento-anomalia` | anomalía z-score destacada | 619 | "¿cuándo hubo saltos raros?" |
| `mercado-dia` | día | 524 | "¿qué está más barato hoy?" |
| `producto-perfil` | producto | 72 | "¿cuánto suele costar en agosto?" |

**9 041 chunks históricos** más una ventana reciente de ~160 que se reconstruye cada día (el corpus pasado es inmutable; solo se reindexan las últimas semanas).

### Por qué semanal: la medición, no la intuición

`evals/run_retrieval.py --granularidad todas` corre el mismo gold set sobre los tres granos. Los predicados piden que un chunk *cubra una fecha*, no un id concreto, así que la comparación es justa:

| grano | chunks | recall@10 | MRR | span evidencia | ms/consulta |
|---|---|---|---|---|---|
| día | 38 561 | 0.897 | 0.711 | **1 día** | 30.8 |
| semana | 9 125 | **1.000** | 0.816 | 5 días | 9.0 |
| mes | 3 078 | **1.000** | **0.955** | 29 días | 3.5 |

Lo que se confirmó: el grano **diario recupera peor**. Días contiguos generan texto casi idéntico, los embeddings quedan casi duplicados y el top-k se llena con la misma semana del mismo producto.

Lo que **refutó** la hipótesis inicial: el grano mensual no recupera peor — recupera **mejor** (MRR 0.955 contra 0.816). Con 4× menos chunks compitiendo, el correcto sube. La conjetura de que "el mensual esconde el pico intra-mes" no la sostiene ni recall ni MRR.

Por eso se agregó **span de evidencia**: cuántos días cubre el chunk que se recuperó. Ahí sí aparece el costo del mensual — satisface el predicado igual, pero le entrega al modelo ~20 días hábiles promediados en vez de ~5. El pico que la pregunta busca queda diluido en el promedio.

**Semanal es el único grano en la frontera de ambas cosas.** Ese es el argumento, y es medible: recall/MRR miden si el chunk correcto *se encuentra*; span mide si *sirve para responder*.

### Búsqueda híbrida

1. **Pre-filtro estructurado.** Se parsea la pregunta por producto y fecha, y se aplica como `WHERE` **antes** del k-NN. Con 72 nombres canónicos el match léxico es *confiable* — ventaja del dominio, no limitación. Dos niveles: ≥2 tokens del nombre identifican una variedad ("ajo morado" vs "ajo criollo"); 1 token agrupa la familia ("papa" son diez variedades). Todo por token completo, nunca subcadena: si no, "papa" matchearía dentro de "papaya".
2. **BM25** sobre el texto de los chunks: atrapa cifras exactas y nombres raros que el vector pierde.
3. **Vectores**: atrapan sinónimos y paráfrasis que el léxico pierde.
4. **Fusión RRF**, que combina por *posición* y evita calibrar dos escalas incomparables (BM25 no está acotado; el coseno vive en [-1,1]).

Las fechas se resuelven contra `latestFecha`, **no contra el reloj**: si el pipeline no corrió hoy, "esta semana" tiene que ser la última semana *con datos*, no una ventana vacía del calendario.

### El piso determinista

La pieza que hace esto utilizable en un producto real. Si la pregunta nombra un producto, su ficha, sus últimas ventanas y sus anomalías se adjuntan **siempre**, gane lo que gane la recuperación.

El motivo es de producto: un asistente que a veces falla lo obvio ("¿a cuánto está la papa?") destruye la confianza en *todas* sus respuestas, incluidas las buenas. La búsqueda probabilística sirve para *descubrir* contexto, no para *garantizarlo*.

Está acotado a 14 chunks y se reparte entre los productos mencionados: muchas variedades → la ficha de cada una; un solo producto → su historia a fondo. Sin tope, "papa" metía 80 chunks (~50 KB) al prompt.

Las evaluaciones lo miden por separado, y el resultado justifica tenerlo: piso solo **1.000**, búsqueda sola **0.793**. Son complementarios, no redundantes.

### Almacenamiento: tres backends tras una interfaz

| Backend | Rol |
|---|---|
| `NumpyIndex` | **Oráculo exacto.** A ~9 200 chunks el k-NN es un producto matriz-vector: ~1 ms, más rápido que cualquier índice aproximado. Los tests verifican contra él. |
| `SqliteVecIndex` | Persistente, sobre la SQLite que el proyecto ya usa. |
| `PgVectorIndex` | Destino de producción; el único que hace ANN filtrado en el motor. |

A esta escala la fuerza bruta alcanza de sobra, y decirlo es parte del diseño: tener una implementación obviamente correcta contra la cual validar las optimizadas es mejor ingeniería que tener solo las optimizadas y confiar.

> **`PgVectorIndex` todavía no se ejecutó contra un Postgres real.** En esta máquina no hay ninguno. Su test existe y está marcado `skipif`; se activa en cuanto `DATABASE_URL` apunte a un Postgres con la extensión, que es lo que trae el docker-compose del bloque 5. Hasta entonces no afirmamos que funcione.

**Orden reproducible.** Los scores empatan seguido (semanas de variedades distintas de papa puntúan igual) y los backends calculan el mismo coseno por caminos distintos, con ~1e-6 de diferencia en float32. Sin criterio explícito, `recall@k` oscilaría entre corridas. La clave de orden se cuantiza y se desempata por id de chunk. Eso garantiza reproducibilidad *dentro* de un backend; la identidad exacta *entre* backends no es alcanzable en float32, y la propiedad que los tests exigen es que devuelvan resultados **igual de relevantes** — mismos scores posición a posición.

### Embeddings: tres proveedores, un contrato

DeepSeek —el proveedor de toda la capa IA— **no tiene endpoint de embeddings**; sus docs solo documentan `chat/completions`. Hace falta un segundo proveedor.

- **`ApiEmbedder`** construye el índice que se publica y embebe la consulta en producción. Es obligatorio para el sitio: el índice va estático, pero la pregunta se embebe en tiempo de request y Vercel no puede correr un modelo local. Dos proveedores verificados, ambos OpenAI-compatibles y sin tocar código: **Gemini** (`gemini-embedding-001`, free tier, respeta `dimensions=256`) y **OpenAI** (`text-embedding-3-small`). El corpus son ~1,5 M de tokens: con OpenAI el backfill cuesta **USD 0.03** y tarda un minuto; con el free tier de Gemini es gratis pero su cuota de 100 embeddings/minuto lo estira a **~1,5 h** (la corrida diaria, de ~165 chunks, tarda ~2 min).
- **`LocalEmbedder`** para quien clona sin claves. Usa **model2vec** (inferencia solo-numpy, ~30 MB) y no sentence-transformers + torch (~2,5 GB): el pipeline entero pesa menos que torch.
- **`FakeEmbedder`** para tests y CI: determinista, sin red, sin modelo, sin claves.

**Consecuencia no obvia:** el índice publicado y la consulta en producción deben usar el **mismo** modelo, o el coseno compara espacios vectoriales distintos y devuelve ruido con total confianza — el peor modo de falla, porque no se nota. Cada índice guarda la firma `{proveedor}:{modelo}:{dims}` y tanto Python como TypeScript se niegan a consultar con otra.

Eso obliga a definir `EMBED_*` **en dos sitios**: en el entorno que construye el índice y en Vercel, que es donde se embebe la pregunta. Ya pasó una vez que solo estuviera en el primero: el sitio siguió respondiendo con el piso determinista y etiquetando `llm-rag`, sin recuperación vectorial y sin que nada fallara. Por eso ahora hay un guard de integridad del artefacto en CI (`pytest -m publicado`) y `ingest --index` se niega a publicar un índice `local:`.

### El índice en producción

El sitio es estático + `snapshot.json`. Meterle una base de datos en el camino de cada pregunta sería cambiar su arquitectura por una capacidad que cabe en ~3 MB, así que el índice **viaja en el deploy**.

**Costo honesto de esa decisión: en producción pgvector no participa.** Vive en el pipeline y en el docker-compose del bloque 5.

**Partición histórico/reciente.** El corpus pasado es inmutable: la semana del 3 al 7 de agosto de 2026 no cambia nunca. Solo se mueven la ventana en curso, el día más reciente, las anomalías nuevas y las fichas. Sin aprovechar eso, el índice completo se reescribiría cada día hábil — y como git guarda cada blob binario entero, serían ~550 MB al año.

| Parte | Chunks | Tamaño | Cambia |
|---|---|---|---|
| `rag-historico.{bin,json.gz}` | 9 041 | ~3.0 MB | rara vez |
| `rag-reciente.{bin,json.gz}` | 165 | ~59 KB | a diario |

**Cuantización.** Los vectores van en int8. Como los embeddings son unitarios, escalar por 127 usa el rango entero completo; el error por componente es 0.0039 (exactamente la cota teórica) y el de score, 0.0046 máximo.

Eso sí reordena casi-empates: medido, el conjunto top-10 coincide con el índice exacto ~4 de cada 8 veces, porque en este corpus muchos chunks están separados por menos de 0.005. **El índice del sitio es aproximado**; el exacto está en el pipeline. Lo que los tests acotan es que ningún puesto se degrade más de 0.02.

### Lo que el sitio corre, y por qué cambió

`web/lib/rag.ts` hace filtro estructurado, **BM25**, búsqueda vectorial, fusión RRF y piso determinista: la misma escalera que el pipeline.

BM25 estaba deliberadamente fuera. El argumento era bueno: portarlo a TypeScript son dos implementaciones de las mismas reglas, que divergen en cuanto se toque una, y el camino correcto era que el sitio llamara a la API del pipeline en vez de copiarla.

Lo que cambió el cálculo es de dónde viene el riesgo. El embebido de la **consulta** ocurre en cada request contra un proveedor externo con cuota por minuto (el free tier de Gemini son 100 embeddings/min). Cuando esa cuota se agota, el sitio se quedaba sin ningún recuperador: solo el piso determinista. BM25 no depende de nadie — corre sobre el texto que ya viaja en el deploy— así que es la única pieza de recuperación que no se puede caer. **Medido:** construir el índice léxico sobre los 9 206 chunks cuesta 318 ms una vez por proceso (y es perezoso), y 3,85 ms por consulta.

### El contrato de conformidad

Que haya dos implementaciones sigue siendo deuda, pero la garantía de que no divergen dejó de ser **convencional** —espejar cada test a mano, y confiar en acordarse— para ser **por construcción**.

`pipeline/tests/conformidad.py` genera desde `retrieval.py` —la implementación de referencia, la que usan las evaluaciones, la CLI y la API— las salidas esperadas de 31 preguntas, 6 consultas BM25 y 6 fusiones RRF. `web/test/conformidad.test.ts` exige que el sitio las reproduzca exactamente. El ciclo se cierra solo:

1. cambias `retrieval.py` → `test_conformidad.py` falla, diciendo qué caso cambió
2. regeneras el fixture → pasa
3. `rag.ts` ya no lo reproduce → el vitest falla
4. alineas `rag.ts` → pasa todo

Se comparan **orden e ids**, no scores crudos: los dos lenguajes usan float64 pero acumulan en distinto orden, y dos scores separados por 1e-16 no significan nada. El desempate por id —que ambas implementaciones aplican— es lo que hace el orden total y comparable.

**El contrato está probado contra deriva real**, no solo escrito: cambiar la constante de RRF en el sitio rompe 6 casos; quitar una palabra vacía rompe 1, señalando la pregunta exacta; mover el umbral de longitud de token rompe 1. Ese ejercicio encontró además un agujero — el catálogo del fixture no incluía ninguno de los tres nombres reales que ejercitan el filtro de tokens (`Manzana Cte/Para Agua`, `Maiz Marlo/Coronta De Maiz`, `Ajo Criollo O Napuri`), así que esa regla no estaba contratada. Ahora sí.

### La solución de fondo, disponible

`web/lib/api.ts` cierra el círculo: si se define `PRECIOVIVO_API_URL`, `/api/consulta` recupera el contexto llamando a `POST /recuperar` del pipeline en vez de usar el port de TypeScript. Ahí el sitio usa **la misma implementación** que las evaluaciones, la CLI y el servidor MCP — incluido el parser de fechas completo, que el port no cubre entero.

El port no se borra, y no es pereza: el sitio es estático + snapshot, y esa propiedad vale más que la elegancia de tener una sola ruta. Si la API no está configurada, tarda demasiado o devuelve cualquier cosa, se responde igual con el port local; el cliente **nunca lanza**, devuelve `null` y la degradación se declara en un solo sitio. La respuesta lleva `motor: "api" | "local"` para que se sepa cuál corrió.

Mientras el respaldo exista, el contrato de conformidad sigue siendo lo que impide que derive.

### Degradación

`/api/consulta` baja peldaños, y cada uno responde con datos reales; lo que cambia es cuánta evidencia ve el modelo:

1. **RAG híbrido** (`llm-rag`) — BM25 + vectores + piso determinista.
2. **RAG léxico** (`llm-rag-lexico`) — BM25 + piso, sin vectores. Si el proveedor de embeddings falla o agota su cuota. Sigue siendo recuperación real sobre el histórico.
3. **Catálogo** (`llm`) — una línea por producto, solo hoy. Si falta el índice.
4. **Fallback** (`fallback`) — palabras clave, sin modelo. Si falta `AI_API_KEY`.

Cada peldaño se **declara** en la respuesta y en la interfaz. No es cosmético: antes el peldaño 2 se reportaba como `llm-rag` —la búsqueda no corría y la respuesta salía igual, diciendo que sí— y la interfaz, que ni siquiera conocía el valor `llm-rag`, anunciaba toda respuesta con RAG como "coincidencia de palabras clave". Una degradación que no se declara es indistinguible de que todo funcione.

### Limitaciones conocidas

- **El pollo vivo no está en el corpus.** Llega por SISAP y vive en `snapshot.mercados`; el corpus se construye solo sobre productos GMML. Documentado como caso en el gold set.
- **La detección de anomalías es hipersensible.** El barrido histórico marca ~4 900 sobre ~74 700 observaciones (6,5%) con umbral z=3.5. No es un bug: la MAD de un precio mayorista es diminuta porque el mismo precio se repite días seguidos, así que cualquier movimiento real supera 3,5 desviaciones robustas. El detector del dashboard tiene la misma propiedad. Aquí solo se acota **cuántas reciben chunk propio** (las 5 más extremas por producto y tipo, más todas las del último día); las demás siguen apareciendo dentro del chunk de su periodo.
- **Las cifras de las tablas de arriba salieron con `FakeEmbedder`**, que mide mecánica (filtros, piso, fusión), no calidad semántica. `evals/run_retrieval.py` siempre imprime cuál usó.

### Medido con un embebedor real

Ya no es una promesa: el índice publicado se construyó con `jina-embeddings-v3` y el gold set se corrió contra él.

> **Esta tabla es de la versión de 32 casos del gold set.** Hoy son 62, con un
> reparto 70/30 fácil-adversarial. No se ha vuelto a correr contra Jina porque
> reindexar cuesta cuota y el backfill histórico va a obligar a rehacerlo de todos
> modos; se actualizará entonces. Las cifras de abajo siguen siendo ciertas para lo
> que midieron, y por eso se dejan con su fecha en vez de borrarlas o de
> reemplazarlas por números de otro embebedor, que no serían comparables.

| | FakeEmbedder | Jina (real) |
|---|---|---|
| recall@k | 1.000 | **1.000** |
| solo lo recuperado (sin el piso) | 0.759 | **0.862** |
| solo el piso (sin la búsqueda) | 1.000 | 1.000 |
| MRR | 0.816 | 0.816 |
| violaciones | 0 | **2** |

Dos lecturas, y la segunda incomoda.

**La búsqueda mejora de verdad**: aislada del piso determinista sube de 0.759 a 0.862. Es la primera evidencia de que la parte semántica aporta y no solo decora.

**Y aparece un falso positivo que el embebedor de juguete escondía.** El caso `papaya-no-es-papa` pregunta por un producto que **no está en el catálogo** y exige que no se cuelen papas. `FakeEmbedder` lo pasaba por construcción —es una bolsa de tokens hasheados, así que "papaya" y "papa" caen lejos—. Un embebedor semántico las pone cerca y devuelve fichas de papa.

Se probó el arreglo obvio, un umbral de coseno, y **no funciona**:

| consulta | coseno máximo |
|---|---|
| `papaya` (no existe en el catálogo) | 0.6444 |
| `papa` (producto real) | 0.6656 |
| `zanahoria esta semana` (producto real) | 0.6790 |
| `¿qué está más barato hoy?` (agregada) | 0.5151 |

Dos centésimas separan una consulta legítima de una que no lo es, y cualquier umbral que las corte mata también las preguntas agregadas. No hay corte posible: papaya y papa **son** parecidas, y una búsqueda por vecino más cercano siempre devuelve algo.

El arreglo va donde sí hay información: el filtro léxico **ya sabe** que ninguna palabra de la pregunta coincide con el catálogo, y esa señal se perdía antes de llegar al modelo. Ahora el prompt lleva un aviso explícito —"NO está entre los que seguimos: dilo, y no respondas con el precio de otro producto por parecido"—. La recuperación sigue devolviendo lo más cercano, que es su trabajo; lo que cambia es que el modelo sabe que es lo más cercano y no una coincidencia.

La evaluación **sigue reportando las 2 violaciones**. No se relajó el gold set para que pasara: el caso mide un riesgo real y el arreglo es del prompt, no de la recuperación.

## Una sola lógica, cuatro transportes

La capa RAG dejó una deuda declarada: `web/lib/rag.ts` reimplementaba en TypeScript el parseo de la pregunta porque el sitio no tenía a quién preguntarle. Al sumar API, MCP y agente, esa duplicación se habría multiplicado por cuatro.

`pipeline/preciovivo/service.py` es la respuesta: las operaciones de negocio —resolver un producto, validar un rango, comparar, pronosticar, recuperar— viven ahí, en funciones puras que no saben de HTTP ni de MCP ni de LangGraph. Cada transporte es una capa delgada.

```
                    ┌──────────────┐
   API REST ───────►│              │
   Servidor MCP ───►│  service.py  │───► corpus · retrieval · forecast · snapshot
   Agente ─────────►│              │
   CLI ────────────►└──────────────┘
```

Fuente única: el **snapshot**, no SQLite. Es función pura de un artefacto versionado, y garantiza que la API, el MCP, el agente, la CLI y el dashboard digan exactamente lo mismo. Un endpoint que contradiga al gráfico de al lado destruye la confianza en ambos.

## API REST (FastAPI)

```bash
pip install -e "pipeline[api]"
PRECIOVIVO_API_KEYS=tu-clave python -m preciovivo.api    # http://127.0.0.1:8000/docs
```

13 rutas con OpenAPI generado: catálogo, precio vigente, serie histórica, comparación, pronósticos, anomalías, verificación SISAP, consulta NL y `/recuperar` (el contexto RAG sin llamar al modelo, para poder auditar una respuesta).

**Autenticación que falla cerrada.** Sin `PRECIOVIVO_API_KEYS`, los endpoints de datos devuelven **503**, no datos abiertos. Un default abierto es la clase de decisión que nadie revisa hasta que la API lleva meses pública; preferimos que rompa en el primer request de desarrollo. Para abrirla a propósito está `PRECIOVIVO_API_ABIERTA=1`, que es explícito y auditable. `/health` responde siempre: si la puerta está cerrada, hay que poder preguntarle por qué.

**`/health` reporta el estado real del dataset**, no un `ok` fijo. Un servicio que responde 200 mientras sirve datos de hace dos semanas es peor que uno caído, porque nadie se entera.

**Los errores enseñan.** `GET /productos/papa` no devuelve un 404 pelado: devuelve *"'papa' coincide con 10 productos. Sé más específico: papa-amarilla, papa-blanca…"*. El catálogo tiene nombres como `Ajo Criollo O Napuri` que nadie escribe enteros.

## Servidor MCP

Precios mayoristas peruanos para cualquier cliente MCP. Hasta donde sabemos no existía uno.

```bash
pip install -e "pipeline[mcp]"
```

```jsonc
// Claude Desktop: claude_desktop_config.json
{
  "mcpServers": {
    "preciovivo": {
      "command": "preciovivo-mcp",
      "env": { "PRECIOVIVO_EXPORT": "/ruta/a/PrecioVivo/web/data/snapshot.json" }
    }
  }
}
```

8 herramientas de solo lectura (`precio_actual`, `serie_historica`, `pronostico`, `anomalias`, `comparar_productos`, `buscar_productos`, `consultar`, `cobertura`) más el catálogo como recurso.

**Se diseña para un LLM, no para un programa** — y eso condiciona todo:

- **Las series se resumen, no se vuelcan.** Un endpoint REST puede devolver 519 puntos y que el cliente pagine; una herramienta MCP escribe en la ventana de contexto de un modelo, donde ese volcado llena espacio, diluye la señal y se paga por token. `serie_historica` muestrea a 60 puntos **de forma uniforme** —no los últimos 60— para conservar la forma de la serie: quedarse con la cola escondería justo el pico viejo que la pregunta busca. El dato más reciente nunca se pierde.
- **Cada salida se explica sola.** El modelo que la lee no vio este README. Un pronóstico lleva su advertencia y una anomalía dice que no explica causas, en la misma respuesta.
- **Los errores enseñan en vez de abortar.** Un producto ambiguo devuelve las opciones como *resultado*, no como excepción de transporte, para que el modelo se corrija sin otra vuelta con el usuario.
- **`consultar` devuelve contexto, no una respuesta redactada.** El cliente MCP ya es un modelo capaz de escribir; llamar a otro LLM ahí agregaría costo, latencia y una segunda oportunidad de alucinar, para producir texto que el cliente igual reescribe.
- **`cobertura` dice qué NO cubre**: sin precios minoristas, sin otras ciudades, sin otros países. Es lo que evita que el modelo intente responder algo que estos datos no pueden.

## Agente con orquestación explícita (LangGraph)

```bash
pip install -e "pipeline[agente]"
AI_API_KEY=... python -m preciovivo.agent "¿por qué subió la papa esta semana?"
```

LangGraph trae un ReAct prearmado en una línea (`create_react_agent`). Acá el `StateGraph` se construye a mano, y no por gusto: **el prearmado esconde justo lo que hay que poder mostrar y ajustar.**

| Nodo | Por qué existe |
|---|---|
| `razonar` | Decide si pedir más datos o responder. |
| `ejecutar` | Corre las herramientas. No usa `ToolNode` porque hay que clasificar cada fallo. |
| `rendirse` | Se agotó el presupuesto: responde con lo que se averiguó y dice qué faltó. |

Tres decisiones que el prearmado no deja tocar:

1. **El presupuesto de iteraciones.** Un agente en bucle es el modo de falla más caro que existe: no se cae, **factura**. Acá el corte es un contador en el estado y un borde condicional — inspeccionable y testeable.
2. **La política de reintento.** No todos los fallos merecen reintento. Un producto inexistente no se arregla repitiendo la llamada; se arregla diciéndole al modelo que busque el nombre. Un timeout de red sí se reintenta. Esa distinción es una función con nombre, `_clasificar_fallo`, no un `try/except` genérico.
3. **La respuesta al agotar el presupuesto.** El prearmado devuelve el último mensaje, que puede ser una llamada a herramienta a medio hacer. `rendirse` sintetiza — y si el modelo también falla ahí, hay un guard que garantiza que el turno **nunca** quede vacío. Ese guard lo encontró un test, no una revisión.

`responder()` devuelve una `Traza` con la respuesta, las herramientas usadas, las iteraciones y los fallos. Un agente que solo devuelve texto es imposible de depurar.

## Docker

```bash
cp .env.example .env
docker compose up                                      # db + api + web
docker compose --profile indexar run --rm indexer      # reconstruye el índice RAG
docker compose --profile test run --rm tests           # pytest + evals
```

**Qué aporta Postgres acá.** El pipeline corre sobre SQLite y el sitio sobre `snapshot.json`: nada *necesita* Postgres. Está por dos razones concretas: ejercitar `PgVectorIndex` —que nunca se ejecutó contra un servidor real y tiene su test marcado `skipif`— y ser el camino documentado a producción. Levantando solo `api` y `web`, todo funciona sin base.

Detalles que no son accidentales: ambas imágenes corren como **usuario sin privilegios**; los datos se **montan** como volumen en vez de hornearse, porque el snapshot cambia cada día hábil y reconstruir la imagen por eso sería absurdo; el `healthcheck` de la API consulta `/health`, que mira la frescura del dato y no solo si el proceso vive.

> **No verificado:** el daemon de Docker no corría en la máquina donde se escribió esto. Los archivos están completos y revisados, pero `docker compose up` nunca se ejecutó.

## CI/CD

**`ci.yml`** — en cada push y PR, tres jobs independientes (pipeline, evaluaciones, sitio) para que un fallo del sitio no oculte uno del pipeline. Corre ruff, los 416 tests de pytest, **el guard de integridad del índice publicado**, `tsc`, eslint, los 218 de vitest, el build de Next, y **las evaluaciones de recuperación con umbral**: sin ese gate, una regresión en el RAG pasaría verde porque ningún test unitario la ve.

El CI usa `FakeEmbedder`: determinista, sin red y sin secretos. Eso mide **mecánica**, no calidad semántica — el README lo dice y el runner lo imprime en cada corrida.

**`ingesta-diaria.yml`** — con una salvedad grande y documentada arriba del archivo:

> El paso de descarga **probablemente falle** en GitHub Actions. El WAF de gob.pe no bloquea una IP residencial peruana, pero sí puede bloquear rangos de datacenter, que es donde viven los runners. Está anotado desde el día 1 en `DEPLOY.md §3`.

Por eso el job de ingesta está **desactivado por defecto** (`vars.INGESTA_EN_CI`) y listo para el día que haya proxy residencial o VPS en Perú. Lo que sí aporta valor hoy corre desde cualquier IP:

**El dead-man's-switch.** El modo de falla de este proyecto no es caerse: es dejar de actualizarse en silencio. El sitio sigue en pie, responde 200, muestra precios con formato impecable — de hace once días. `scripts/frescura.py` mide lo único que importa: cuántos **días hábiles** pasaron desde el último dato (hábiles y no naturales, porque el GMML no publica fines de semana; los feriados peruanos se descuentan con `holidays`). Si pasa el umbral, abre un issue — y **reusa el issue abierto** en vez de crear uno por día, porque un pipeline muerto una semana generaría cinco issues idénticos y la señal se perdería en el ruido.

## Fuente y atribución

Datos derivados de reportes públicos de **MIDAGRI – GMML** y mercados mayoristas de Lima.
Precio Vivo republica únicamente **hechos numéricos** (precios, volúmenes) procesados y normalizados; **no** redistribuye los documentos fuente.

**Cifras referenciales, no oficiales.** Fuente original: MIDAGRI / EMMSA.

## Estructura

```
pipeline/          Pipeline + CLI (Python): harvester, parser coordenado, forecast (GBM),
                   capa IA (DeepSeek) y la CLI `precio`
  corpus.py          hechos numéricos -> chunks de texto (4 tipos)
  embeddings.py      embebedor agnóstico: API | local (model2vec) | fake (tests)
  vectorstore.py     índice: NumpyIndex (oráculo) | SqliteVecIndex | PgVectorIndex
  retrieval.py       búsqueda híbrida: filtro + BM25 + vectores + RRF + piso
  indexer.py         construcción y publicación del artefacto estático
  evals/             gold set de recuperación + runner con umbral
web/               Dashboard (Next.js 16) + API de consulta en lenguaje natural
  lib/rag.ts         recuperación del sitio sobre el índice estático
  data/rag-*.{bin,json.gz}   índice publicado (histórico + cola reciente)
data/              Archivos crudos y dev DB (local-only, fuera de git)
PLAN.md            Plan de ejecución (v4)
```

## Desarrollo

Requiere Python 3.11+ y Node 20+. Setup del pipeline:

```bash
cd pipeline
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -r requirements.txt
pytest                      # 425 pruebas (416 unitarias + 9 del artefacto), sin red ni claves
```

Reconstruir el índice RAG (necesita el snapshot ya exportado):

```bash
python -m preciovivo.ingest --index                   # completo
python -m preciovivo.ingest --index --index-solo-reciente   # el modo diario

# ¿funciona? mide, no adivines:
python evals/run_retrieval.py --k 10 -v
python evals/run_retrieval.py --granularidad todas    # la tabla de arriba
```

Sin `EMBED_API_KEY` el índice se construye con el embebedor local (model2vec); ese
índice sirve para la CLI pero **no** es el que debe publicarse, porque el sitio
necesita embeber la consulta con el mismo modelo y no puede correr uno local.

Para tantear sin instalar nada: `PRECIOVIVO_EMBED_PROVIDER=fake ... --no-publicar`.
Publicar un índice construido con el embebedor de juguete está **bloqueado**: el
guard de firma del sitio lo rechazaría de todos modos, pero en silencio —
`/api/consulta` degradaría a catálogo-en-contexto y nadie notaría que en
producción nunca hubo RAG. Mejor romper donde se ve.
