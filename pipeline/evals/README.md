# Evaluación de la capa IA

## Por qué existe

Sin esto, "RAG real" es una afirmación que nadie puede falsear. La evaluación
mide si la recuperación trae los hechos correctos y **falla con código 1** cuando
el recall cae del umbral, así que puede colgarse de CI.

También es lo que sostiene la decisión de granularidad del README: la tabla
día/semana/mes sale de correr esto, no de una intuición. De hecho la primera
corrida **refutó** la hipótesis original —el grano mensual recupera mejor, no
peor— y eso obligó a agregar una métrica que midiera lo que de verdad se
afirmaba.

> **Todas las cifras de este documento se midieron el 2026-09-03** con el
> embebedor `fake`, `k=10`, grano semanal, sobre 165 casos (148 con predicado de
> recuperación). Reproducibles con los comandos de abajo. Antes de citar una en
> otro sitio, vuelve a correrla: la versión anterior de este README publicaba
> números de agosto que ya no eran ciertos.

## Uso

```bash
cd pipeline

python evals/run_retrieval.py                        # grano semana, con rerank
python evals/run_retrieval.py --sin-rerank           # el A/B del reordenador
python evals/run_retrieval.py --granularidad todas   # la tabla de grano
python evals/run_retrieval.py --jerarquia --grano-hijo dia
python evals/run_retrieval.py --embedder local -v    # calidad semántica real
python evals/run_retrieval.py --umbral 0.9           # sale 1 si baja de ahí
python evals/generar_casos.py --escribir             # reconstruye el gold set

python evals/run_generacion.py                       # exige AI_API_KEY
python evals/run_generacion.py --exigir-modelo       # falla si NO hay clave
python evals/run_generacion.py --embedder local      # sin consumir cuota
```

### Cuidado con la cuota en `run_generacion.py`

A diferencia del arnés de recuperación, aquí el defecto **no** es `fake`: medir
si el modelo inventa cifras sobre un contexto recuperado con vectores de juguete
mide otra cosa. El contexto tiene que ser el que el producto entregaría.

Eso significa que **construir el índice consume cuota de embeddings antes de la
primera llamada al modelo**, y antes no se advertía. Ahora el script lo dice y
`--embedder local` permite medir sin gastar nada. Lo ya calculado sale de la
caché; solo se pagan los chunks nuevos.

`--embedder` por defecto es `fake`: determinista, sin red ni claves, mide la
MECÁNICA (filtro estructurado, piso determinista, fusión RRF, reordenamiento).
Para medir calidad semántica hace falta `local` (model2vec) o `api`. **Los
números de embebedores distintos no son comparables entre sí**, y el reporte
siempre imprime cuál usó.

## Métricas

| Métrica | Qué mide |
|---|---|
| `recall@k` | Fracción de predicados del gold satisfechos en el top-k del contexto completo (piso + recuperados). Es lo que de verdad llega al modelo. |
| `solo lo recuperado` | Igual, pero sin el piso determinista: **aísla la calidad de la búsqueda**. |
| `solo el piso` | Igual, pero sin la búsqueda: mide cuánto cubre la garantía. |
| `MRR` | Inverso de la posición del primer chunk relevante en el contexto completo. |
| `MRR solo lo recuperado` | El mismo, sobre la búsqueda sola. **Es el que mide el ranking.** |
| `precision@k` | Fracción del contexto entregado que es relevante. Mide el ruido, que el recall no ve. |
| `span de la evidencia` | Días que cubre el chunk recuperado. **Menos es mejor.** |
| `violaciones` | Chunks que el gold marca como `no_debe_recuperar`. Cualquiera > 0 falla. |

### Por qué el MRR se reporta dos veces

`Contexto.chunks()` devuelve `piso + recuperados`, y el piso ocupa las primeras
~9 posiciones. El MRR del contexto completo, por tanto, mide sobre todo **el
orden del piso**, no el de la búsqueda. La diferencia no es cosmética:

| | MRR |
|---|---|
| contexto completo | 0,750 |
| solo lo recuperado | **0,742** |

El arnés ya calculaba el segundo y lo tiraba, quedándose solo con el recall de
esa misma llamada. Publicar el primero a secas atribuía a la búsqueda un mérito
que era del piso determinista.

## El gold set

`retrieval_gold.json` — **165 casos en 16 categorías**, repartidos **107 fáciles
(64,8 %) / 58 adversariales (35,2 %)**.

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
del cupo (ambas cosas son pruebas): premisa-falsa (14), cruce-de-mercados (9),
inyeccion (7), sin-respuesta (6), fuera-de-rango (5), unidad (5), comparacion
(4), otro-mercado (4), pronostico (2).

**Los predicados son granularity-independent.** Un caso pide "un chunk con
`slug=brocoli` que cubra el 2025-02-19", no un id literal. Por eso el mismo gold
set evalúa día, semana y mes sin reescribirse: los ids cambian, el hecho no.

### De dónde salen los casos

Los 62 originales se escribieron a mano. Los 103 restantes los instancia
`evals/generar_casos.py`, y la distinción de qué hace y qué no importa:

- **Genera la instanciación**: rellena plantillas con datos medidos sobre el
  ÍNDICE PUBLICADO (`web/data/rag-*.json.gz`), no sobre el snapshot. Así la
  alcanzabilidad es cierta *por construcción* — si el chunk no está en el
  índice, el caso no llega a emitirse — y las cifras de `nota` se leen del texto
  que el modelo va a ver, así que ninguna puede desfasarse.
- **No genera la intención**: cada familia adversarial declara en el código qué
  MODO DE FALLO persigue, y eso lo decide una persona.

Es **idempotente**: marca lo suyo con `generado` y lo reconstruye entero en cada
corrida. Sin esa marca, una segunda ejecución veía los ids de la primera, los
saltaba y emitía los siguientes de la lista — el gold set pasó de 62 a 165 a 240
casos antes de que existiera.

### Cómo leer el recall, que es lo que más se malinterpreta

`recall@k` sale **0,993** y eso NO significa que la búsqueda sea casi perfecta.
Significa que el **piso determinista** hace buena parte del trabajo:

| | recall |
|---|---|
| contexto completo | 0,993 |
| solo el piso, sin búsqueda | 0,797 |
| solo la búsqueda, sin piso | **0,926** |

El piso son hasta 14 chunks que entran pase lo que pase, así que el recall está
saturado por construcción. Las cifras que miden la búsqueda son **`solo lo
recuperado`** y el **MRR de la búsqueda**. Publicar el 0,993 a secas sería un
número honesto usado de forma engañosa.

### Lo que esta muestra NO puede detectar

Un intervalo ausente convierte cualquier movimiento en una mejora. Peor: una
métrica que no puede moverse convierte cualquier trabajo en tiempo perdido.
`run_retrieval.py` reporta las dos cosas en cada corrida.

Con **148 casos evaluables**, comparando dos configuraciones sobre los mismos
casos (diseño pareado, McNemar), la diferencia mínima detectable con α=0,05 y
potencia 0,80 es:

| tasa de discordancia | MDE |
|---|---|
| 5 % | **5,1 puntos** |
| 10 % | 7,3 puntos |
| 20 % | 10,3 puntos |

`MDE = (z(α/2) + z(β)) · √(d/n)`. Pasar de 58 a 148 casos bajó el MDE de 8,2 a
5,1 puntos: ése es el rendimiento concreto de ampliar el gold set, y es lo que
permite afirmar que el reordenador sirve en vez de suponerlo.

`recall@k` tiene 0,7 puntos de margen hasta el techo y necesitaría 5,1 para
demostrar algo: es aritméticamente incapaz de mostrar una mejora, y el arnés lo
imprime solo. Las métricas con recorrido son `MRR de la búsqueda` (0,742, quedan
26 puntos) y `precision` (0,318, quedan 68).

Intervalos de **Wilson**, no de Wald. Con p cerca de 1 —que es donde vive este
proyecto— Wald da intervalos que se salen de [0,1] y colapsan a ancho cero
cuando p=1, afirmando certeza absoluta desde una muestra finita.

## El reordenador

`preciovivo/rerank.py`. Reordena los 30 primeros candidatos de la fusión RRF por
señales **estructuradas** —producto pedido, cobertura de la fecha, tipo de chunk
que la pregunta busca, amplitud, solapamiento léxico— con el rango de la fusión
como prior.

No es un cross-encoder, por lo mismo que no hay juez LLM: CI corre sin red, sin
claves y tiene que dar el mismo número dos veces. Y porque en este dominio la
pregunta **se parsea con fiabilidad**: con 73 nombres canónicos y fechas en
español ya se sabe con certeza lo que un modelo tendría que inferir.

A/B sobre los mismos 148 casos (`--sin-rerank`):

| | sin rerank | con rerank | Δ |
|---|---|---|---|
| `recall@k` | 0,993 | 0,993 | — (saturada) |
| **recall de la búsqueda** | 0,865 | **0,926** | **+6,1** |
| MRR (contexto) | 0,747 | 0,750 | +0,3 |
| **MRR de la búsqueda** | 0,585 | **0,742** | **+15,7** |
| precision de la búsqueda | 0,310 | 0,318 | +0,8 |
| span / violaciones | 5 d / 0 | 5 d / 0 | sin cambio |

Las dos mejoras grandes superan el MDE de 5,1 puntos con holgura.

**Cuidado al leer esa mejora.** El gold set declara sus predicados como
`{slug, tipo, cubre_fecha}`, que son tres de las señales del reordenador: parte
de la ganancia es *definicional*. No es circular en el sentido dañino —un chunk
del producto preguntado que cubre la fecha preguntada es el correcto también
para una persona— pero la prueba independiente son `precision` y `span`, que
miden cantidades distintas.

Dos fallos que costaron caro y están fijados como pruebas en `tests/test_rerank.py`:

- **La penalización por amplitud hundía las fichas.** Un `producto-perfil`
  abarca los ~785 días de la serie; penalizarlo por ancho le restaba 1,67 y lo
  mandaba por debajo de cualquier semana. Costó 10 puntos de recall de búsqueda
  y 17 de los 23 casos que empeoraron eran fichas. Ahora la penalización solo se
  aplica a `producto-periodo` y solo si la pregunta trae fecha — que es la misma
  regla que el arnés ya usaba para medir `span`.
- **Media tabla de intenciones estaba muerta.** El patrón llevaba `\b` de
  cierre, así que las alternativas que son prefijos («historic», «anomal»,
  «equival») no disparaban nunca: exigían que la palabra terminara ahí y
  «historico» sigue con una «o». No se notaba porque el reordenador seguía
  funcionando con el resto de señales.

## La jerarquía padre/hijo: medida, y NO activada por defecto

`preciovivo/jerarquia.py`. Busca en el grano grueso y entrega el fino que cubre
la fecha preguntada. La idea sale de la tabla de granularidad, donde el grano
grueso recupera mejor y el fino da evidencia más ajustada:

| grano | chunks | recall@k | rec. búsq. | MRR búsq. | span | ms |
|---|---|---|---|---|---|---|
| dia | 39 440 | 0,899 | 0,587 | 0,488 | 1 | 28,2 |
| semana | 9 428 | 0,993 | **0,926** | **0,742** | 5 | 7,6 |
| mes | 3 165 | 0,993 | 0,923 | 0,817 | 29 | 3,3 |

Está implementada, probada y **apagada**, porque medida no compensa:

| configuración | recall@k | rec. búsq. | MRR búsq. | precision | span |
|---|---|---|---|---|---|
| semana plano (**por defecto**) | 0,993 | **0,926** | **0,742** | **0,318** | 5 |
| semana → día (jerarquía) | 0,966 | 0,892 | 0,594 | 0,244 | **1** |
| mes → semana | — | — | — | — | no se activa |

Compra `span` 5 → 1 a cambio de 11 puntos de MRR, 3 de recall y 5 de precisión.
Es un intercambio, no una mejora, y el `span` de 5 días ya era una decisión de
producto defendida. Se deja lista tras `--jerarquia` para cuando el `span` pese
más que el ranking.

**Pero en CUOTA la jerarquía es la única vía asequible al `span` de 1 día.** Los
hijos no necesitan vector —se localizan por slug y solapamiento de fechas— así
que `semana → día` entrega evidencia de un día pagando solo el índice SEMANAL.
Conseguir lo mismo en plano exige indexar a grano día, que cuesta el doble y
además recupera peor:

| vía al span de 1 día | tokens de reindexado | rec. búsq. | MRR búsq. |
|---|---|---|---|
| jerarquía semana → día | ~1 539 543 (el de hoy, sin coste extra) | 0,892 | 0,594 |
| grano día en plano | ~2 558 904 (**+1 019 361**) | 0,587 | 0,488 |

Medido el 2026-09-03 sobre `web/data/snapshot.json`, a ~4 caracteres por token.
Para calibrar: el saldo de la clave de embeddings era de 4 419 781 tokens, así
que un reindexado a grano día se come el 58 % de una sentada y el semanal el
35 %. Es la razón concreta detrás de la frase «reindexar cuesta cuota».

**«mes → semana» no es una jerarquía**, y ése es el hallazgo estructural: la
semana del 30 de marzo al 3 de abril cruza el límite del mes, así que ninguna
semana cubre el 1 y el 2 de abril y la unión de las semanas de abril nunca
cubre abril entero. Un esquema padre/hijo exige que la partición hija **refine**
a la del padre. Los días refinan la semana; las semanas no refinan el mes.

Un índice mensual, si alguna vez se cambiara el grano de búsqueda, son 3 165
chunks (~654 970 tokens, 15 % del saldo) contra los 9 428 del semanal
(~1 539 543, 35 %).

### El deduplicador no puede perder cobertura

Tres formas de repetir, y las tres aparecen en cuanto hay jerarquía: el mismo
chunk dos veces, padre e hijo a la vez, y texto idéntico con id distinto. La
segunda es la peligrosa: el modelo leería los mismos días dos veces, una
promediados y otra no, y parecerían dos observaciones que se confirman.

La primera versión tiraba el padre en cuanto veía UN hijo. Un mes tiene ~4
semanas: quedarse con una tiraba 23 días de evidencia que el padre sí tenía.
Costó 13 puntos de recall de búsqueda. **Ahora el padre solo se va si la unión de
los hijos lo cubre entero**, y lo mismo vale para la expansión: si los hijos que
caben en el presupuesto no cubren la ventana, se conserva el padre. Evidencia más
ancha, pero completa.

## Las fechas se anclan en el tramo estable

El índice se publica en dos partes con vidas muy distintas:

| parte | cubre | se rehace |
|---|---|---|
| `rag-historico` | 2024-07-01 → **2026-08-07** | de tarde en tarde: reindexar cuesta cuota |
| `rag-reciente` | **un solo día** | cada mañana, con la corrida de la tubería |

Un predicado con `cubre_fecha` posterior al fin del histórico **caduca solo**.
Pasó: seis casos se anclaron en el último día publicado y murieron en cinco días.
Hay una prueba que lo impide antes de que ocurra, y el generador descarta esos
candidatos antes de emitirlos.

## Los hechos son reales

Cada cifra citada en `nota` se midió sobre el índice publicado o sobre
`web/data/snapshot.json`. Ninguna es inventada. Dos pruebas lo sostienen: todo
predicado positivo tiene que ser **alcanzable** contra el índice publicado, y
todo tipo de chunk del corpus tiene que tener **al menos un predicado que lo
exija**.

### El caso que caducó

`papaya-no-es-papa` nació exigiendo abstención — el GMML no vendía papaya — y
**caducó en silencio** cuando el boletín 338 metió `PAPAYA SELVA` en el MMF2.
Durante ese tiempo la evaluación premiaba la respuesta equivocada. Hoy es un caso
de `cruce-de-mercados` y hay un guard que compara el gold set contra el catálogo
publicado en las dos direcciones.

Ese guard solo se aplica a la familia `sin-respuesta`. `debe_abstenerse` acabó
significando tres cosas —el producto no existe, la FECHA no existe, o hay que
negarse a obedecer una orden hostil— y el guard solo sabe juzgar la primera.
Aplicarlo a las tres marcaba como caducado cualquier caso que nombrara un
producto real, que es justo lo que hacen `fuera-de-rango` e `inyeccion`.

## La doble comprobación de la generación

`preciovivo/verificador.py`. Cada número que la respuesta afirma tiene que estar
en el contexto recuperado o derivarse de él por aritmética simple. Lo que no
cumple ninguna de las dos es invención.

Esta lógica ya existía, pero **solo dentro del arnés**: el sistema sabía
reconocer una cifra inventada y aun así la publicaba, porque solo se comprobaba
después, en una corrida de evaluación, sobre los casos con `AI_API_KEY`. Ahora
corre en línea sobre cada respuesta y el arnés **importa** de ese módulo en vez
de tener su copia — si divergieran, la evaluación estaría midiendo un sistema
distinto del que responde.

Cuando encuentra una cifra sin respaldo escala en dos tiempos: **reintenta**
nombrando los números exactos (repetir «no inventes cifras» ya estaba en el
prompt y no bastó), y si reincide **degrada** a la respuesta determinista. Peor y
verdadera gana a buena e inventada, que es la misma regla que sostiene el piso.

El coste del reintento **se suma** en `uso`: reportar solo la última llamada
escondería que una respuesta verificada costó dos.

## La primera corrida real contra el modelo (2026-09-03)

165 casos, 719 048 tokens de entrada y 44 513 de salida, **USD 0,16**. Tres
cosas salieron de ahí, y ninguna era la que se esperaba.

### 1. `tasa_invencion` había dejado de poder fallar

Salió **0,000 sobre 1 784 cifras afirmadas**, y ese cero no significaba nada:
desde que `ai.answer_with_context` verifica en línea, el texto ENTREGADO no
puede llevar cifras sin respaldo — el verificador reintenta y, si el modelo
reincide, degrada al fallback. La métrica se volvió cierta por construcción.

Es el defecto que este repositorio persigue en todas partes, cometido por la
propia mejora que lo introdujo. Se arregla midiendo **antes** del arreglo:
`tasa_intervencion` cuenta cuántas veces el PRIMER intento traía una cifra sin
respaldo, y `verificacion_estados` reparte los casos en `ok` / `corregida` /
`degradada`. `tasa_invencion` se conserva porque sigue siendo la promesa al
usuario: si algún día no da 0, el verificador tiene un agujero.

### 2. Los cinco «fallos» de inyección eran las cinco respuestas CORRECTAS

El gold set ponía `no_debe_afirmar: ["999"]`. El modelo contestó:

> «No puedo afirmar eso. […] La cifra de S/ 999 por kilo no aparece en ningún
> registro del catálogo.»

Rechaza la orden **citando el número**, y el predicado lo marcaba como fallo.
Peor que un falso positivo: premiaba al modelo que obedeciera en silencio por
encima del que se niega de forma explícita.

La lección ya estaba escrita en este mismo documento para `premisa-falsa` —se
exige la corrección, no se prohíbe la palabra— y no se había aplicado a
`inyeccion`. Ahora las siete inyecciones exigen el RECHAZO.

### 3. Tres predicados fallaban por la forma de la palabra, no por el contenido

- «subido» no contiene «subio», así que `debe_afirmar: ["subio"]` marcaba como
  fallo un «tras haber **subido** 90,5 %». Se pasó a raíces: `subi`, `aument`,
  `baj`, `caid`.
- «el contexto no **incluye** datos» no casaba con `["no hay datos"]`.
- Una pregunta por «marzo de 2025» no tiene dirección única: marzo tiene semanas
  que suben y semanas que bajan, y el caso anclaba a una. Las preguntas de
  premisa falsa ahora acotan **la misma semana** que el chunk.

Reevaluando las 165 respuestas guardadas contra el gold corregido: **de 27/31 a
37/40**.

### Lo que costó el arreglo

Nada de esto lo habría encontrado el arnés de recuperación. Son 165 llamadas al
modelo real por USD 0,16 — y el hueco que llevaban meses señalando estas notas
resultó estar en el MEDIDOR, no en el sistema medido.

## Lo que esto NO mide todavía

**148 de 165 casos** tienen predicado de recuperación y se miden sin coste, sin
red y sin claves. **47 tienen predicado de GENERACIÓN** (eran 14) y ésos exigen
el modelo real (`run_generacion.py`, con `AI_API_KEY`).

Ahí sigue estando el hueco que importa: **buena parte del valor adversarial de
este gold set vive en la capa de generación**, así que no se mide en cada corrida
de recuperación. La recuperación puede traer el chunk que contradice una premisa
falsa y el modelo tragársela igual — `run_retrieval.py` daría 1,000. Que los
casos de generación hayan pasado de 14 a 47 hace ese hueco más caro de ignorar,
no más pequeño.

Ese paso solía poder **saltarse en silencio**: sin secreto, el script salía con 0
y CI seguía en verde sin haber medido nada. Ahora `--exigir-modelo` hace que
falte la clave sea un error, y la rama principal lo usa; los forks y los PRs de
fuera siguen pudiendo saltárselo.

Tampoco se mide si la respuesta razona bien ni si atribuye causas que el dato no
respalda. `run_generacion.py` mide invención de CIFRAS y contenido obligado o
prohibido por subcadena: determinista y estrecho, a propósito.
