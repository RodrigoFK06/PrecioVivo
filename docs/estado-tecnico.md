# Precio Vivo · Estado técnico

Corte: **2026-08-19**. Todo lo que hay aquí está medido contra el sistema
desplegado, no estimado. Cuando un número no se pudo verificar, se dice.

---

## 1. Qué es el sistema

Inteligencia de precios mayoristas del Gran Mercado Mayorista de Lima. Tres
piezas y una tubería que las une:

```
FUENTES                    PROCESO (AWS)                     SALIDA
─────────                  ─────────────                     ──────
MIDAGRI reporte 335   ┐                                  ┌→ precio-vivo.vercel.app
(PDF diario, gob.pe)  ├→  Step Functions, L-V 08:00 Lima ┤
SISAP (pollo vivo)    ┘    6 Lambdas · S3 · DynamoDB     └→ Function URL (API)
```

La tubería completa, tal como está desplegada:

```
07:30  máquina en Lima ─ relevo SISAP: baja la BD de S3, cosecha, la devuelve

08:00  Cosechar ── ¿pasó la compuerta? ─┬→ CompuertaDeEscritura (Fail)
                                        └→ ContrastarSisap ──[Catch]──┐
                                                 ↓                    │
                                           ListarProductos ←──────────┘
                                                 ↓
                                           PorProducto (Map ×8)
                                                 ↓
                                           Reducir → Exportar
                                                 ↓
                                           IndexarRag ──[Catch]──┐
                                                 ↓               │
                                           PublicarEnGitHub ←────┘
                                                 ↓
                                             Publicado          → Vercel redespliega
```

**Por qué la máquina de Lima sigue ahí.** `sistemas.midagri.gob.pe` no acepta
conexiones desde AWS. Medido en ambos sentidos:

| desde | resultado |
|---|---|
| una IP residencial peruana | 200, 337.994 bytes, 0,3 s, PDF válido |
| Lambda en us-east-1 | `ConnectTimeout` a los 60 s, sin abrir el TCP |

Es la única pieza que no migró, y es un punto único de fallo con forma de
portátil.

---

## 2. Inventario

### Código

| área | líneas | archivos |
|---|---|---|
| `pipeline/preciovivo` | 8.548 | 22 |
| `pipeline/tests` | 4.043 | 22 |
| `infra` (CDK) | 1.893 | 14 |
| `web/components` | 2.408 | 17 |
| `web/app` | 1.332 | 11 |
| `web/lib` | 1.286 | 4 |

**459 pruebas** de pipeline y **218** del sitio, 4 saltadas (dos exigen
Postgres, dos son integraciones lentas tras una variable de entorno).

El CI las corre en cada push, en cuatro trabajos independientes para que un fallo
del sitio no oculte uno del pipeline. Estuvo **apagado por facturación** durante
días —fallaba en 3 s en cada push— y su primera corrida real, ya desbloqueado,
encontró un fallo que las pruebas locales no podían ver: el guard del índice
comparaba contra `pipeline/.env`, un archivo no versionado, así que en local
pasaba por casualidad. Un guard que depende de un archivo que no está en el
repositorio no es un guard.

Debajo había algo peor: los defaults de `EMBED_MODEL` apuntaban a un modelo que
no se usa en ningún sitio del proyecto. En `web/lib/rag.ts` eso era un peligro
activo — perder esa variable en Vercel habría apagado la recuperación vectorial
**sin error visible**, que es exactamente el fallo del primer commit de esta
iteración.

### Infraestructura

Cuenta `813559109370`, región `us-east-1`. Todo en CDK/Python; ni un recurso
creado desde la consola.

| stack | recursos |
|---|---|
| `CDKToolkit` (bootstrap) | 12 |
| `PrecioVivoFase1` | 14 |
| `PrecioVivoFase3` | 39 |

### Estado en S3

```
estado/preciovivo.db                       7,6 MB   SQLite, fuente de verdad
estado/embed_cache.api_jina-...256.npz    12,2 MB   9.523 vectores cacheados
estado/forecast_cache.json                26,8 KB   lo que calcula el forecast
estado/sisap_check.json                    2,0 KB   el contraste
rag/rag-reciente.{bin,json.gz}            55,9 KB   ventana reciente del índice
snapshot.json                              3,3 MB   lo que sirve el sitio
```

### Datos

537 fechas (2024-07-01 → 2026-08-19), 37.893 filas de precio, 73 productos.
Corpus RAG: **9.281 chunks** con firma `api:jina-embeddings-v3:256`.

---

## 3. Qué cambió en esta iteración

Nueve commits sustantivos. En orden:

1. **Publicar el índice RAG completo.** El sitio decía hacer RAG y no lo hacía:
   el índice publicado tenía firma `local:` —imposible de usar desde Vercel— y
   le faltaba la parte histórica. `rag.ts` detectaba la incompatibilidad, caía al
   piso determinista y **seguía etiquetando la respuesta como `llm-rag`**. De 165
   chunks visibles a 9.206.
2. **Fases 0-2 en AWS.** Snapshot servido desde Lambda; telemetría en DynamoDB.
3. **Fase 3.** Ingesta diaria con EventBridge Scheduler y Step Functions.
4. **Contraste SISAP** como paso propio, con `Catch`.
5. **Indexado RAG en AWS**, con el guard que protege la cuota de embeddings.
6. **Publicación a GitHub**: la tubería deja de necesitar una máquina encendida.
7. **La máquina de Lima** queda reducida al relevo de SISAP.
8. **Observabilidad** de la escalera de degradación de `/api/consulta`.
9. **El RAG cubre los otros mercados** y `sisap` deja de exigir un parser de PDF.

### Antes / después

| | antes | ahora |
|---|---|---|
| `/api/consulta` en producción | `fuente: fallback` | **`llm-rag`** |
| Chunks que el sitio puede consultar | 165 | **9.281** |
| Verificación SISAP | nunca `vigente` | **12/12 coinciden**, vigente |
| Dónde corre la tubería | un portátil encendido | Step Functions |
| Peso de la portada | 4,82 MB | 0,51 MB |
| Pruebas | ~390 | **435** |
| Gasto en AWS | — | **~0,01 USD** acumulado |

---

## 4. Evaluación técnica

### Lo que sostiene una revisión exigente

**Cada decisión de arquitectura tiene un número detrás.**

| decisión | el número que la justifica |
|---|---|
| Cargar el snapshot fuera del handler | `Init 707,5 ms` contra `p50 1,78 ms` — 397× |
| Repartir el forecast en un `Map` | **52 min** en serie contra un tope de **15** |
| Zip y no imagen de contenedor | **207,6 MB** contra un límite de 250, leído de la API |
| `max_concurrency=8` | cuota de la cuenta: **10**, `Adjustable: FALSE` |
| DynamoDB y no solo CloudWatch | `producto` como dimensión: **44 USD/mes** con 72 productos |
| Calendario L-V | 4.620 transiciones contra 4.000 gratuitas |

Ninguno viene de un tutorial: todos salieron de medir el sistema real, y varios
contradijeron la estimación previa. El caso más claro: estimé 25 s por producto
desde una máquina de escritorio y en Lambda son **39,17 s**.

**El forecast reporta que su propio modelo pierde.** Con 537 días de historia el
GBM ya se evalúa sobre los 72 productos y AR(1) le gana en ambos horizontes
(0,1476 contra 0,1711 a un día). El kill-gate lo dice sin adornos. Un sistema que
publica que su modelo no es el mejor es lo que hace creíble el resto de sus
números.

**Anti-leakage estricto y verificado.** Walk-forward de ventana expansiva con
re-ajuste del GBM en cada origen. Al repartir el cálculo hubo que extraer el
cuerpo de un bucle a una función, y se verificó capturando la salida completa
antes y después sobre los 73 productos reales:

```
antes     1708 bytes  sha256 d259fb94189785e8
despues   1708 bytes  sha256 d259fb94189785e8
```

**IAM mínimo real.** Ni un `grant_read`/`grant_write` de CDK —conceden familias
enteras— ni un `Resource: "*"`. El único sitio donde parecía inevitable un
comodín (KMS para descifrar un SecureString) se comprobó empíricamente y no hacía
falta.

**Contrato de conformidad entre lenguajes.** El parser de consultas está
duplicado en Python y TypeScript por necesidad. Python genera un fixture de 43
casos y el TypeScript tiene que reproducirlo. Es la respuesta correcta a una
duplicación que no se puede eliminar.

**Evaluación en las dos capas, medida por separado.** El gold set son **62 casos**
con un reparto **70/30 fácil-adversarial que impone una prueba**, no la costumbre.
La recuperación tiene umbral en CI (recall@k 1.000, MRR 0.818 con embebedor local).
Ese 1.000 está **saturado por el piso determinista** y no dice que la búsqueda sea
perfecta: bajando a k=1 el recall apenas cae a 0.983, mientras «solo lo recuperado»
—la cifra que sí mide la búsqueda— se desploma de 0.750 a 0.299.

El hueco que queda: **14 de los 19 casos adversariales viven en la capa de
generación**, que solo se mide con el modelo real. La recuperación puede traer el
chunk que contradice una premisa falsa y el modelo tragársela igual, y
`run_retrieval.py` daría 1.000 igualmente. La generación mide lo que la
recuperación no puede ver:

| | |
|---|---|
| Cifras afirmadas en 32 respuestas | **352** |
| Sin respaldo en el contexto | **0** (0,0 %) |
| Abstenciones correctas | **2 / 2** |
| Coste por consulta | **USD 0,001437** |

El caso que justifica separarlas: `papaya-no-es-papa` sigue arrastrando chunks de
papa en la recuperación —son las 2 violaciones que el arnés reporta— y aun así la
respuesta se abstiene. Una violación de recuperación **no** se convirtió en un
fallo de respuesta, y sin medir las dos capas eso sería invisible en ambos
sentidos.

La comprobación es determinista y sin juez LLM: cada número de la respuesta tiene
que estar en el contexto o derivarse de él por resta o variación porcentual. Se
pierde poder —no mide utilidad ni razonamiento— y se gana reproducibilidad.

**Y el detector se prueba contra invenciones conocidas, no contra su salida.** La
primera versión reportó «203 cifras, 0 invenciones» y era falso: descartaba todo
valor menor que 10 para evitar ruido y con eso descartaba **todos los precios**.
Doce pruebas fijan ese caso por nombre.

**Guards sobre el artefacto, no solo sobre el código.** Nueve pruebas marcadas
`publicado` verifican el índice que se sirve: que existan las cuatro partes, que
la firma sea la que el sitio usará, que ambas compartan embebedor, que el `.bin`
cuadre con `n_chunks × dims`. Ningún test unitario veía eso: el artefacto no es
código.

### Lo que no está a ese nivel

**SISAP es un punto único de fallo con forma de portátil.** Si esa máquina está
apagada, el sitio pierde el bloque `verificacion`. El dead-man's-switch lo
detectaría por el envejecimiento del dato, pero solo al día siguiente y solo si
además deja de publicarse el snapshot: un contraste que falta no envejece nada.

**El pronóstico sigue siendo la meta, no el producto.** El andamiaje de
evaluación es mejor que el modelo que evalúa. Está bien reportado y no se infla,
pero "IA que predice precios" describe la ambición.

**Tres claves sin rotar.** DeepSeek, Gemini y Jina pasaron por un canal de chat.
La de Jina además vive ahora en SSM, así que rotarla implica actualizar el
parámetro.

---

## 5. El patrón que dominó la iteración

> Analizado en detalle en [postmortem-fallos-silenciosos.md](postmortem-fallos-silenciosos.md):
> nueve incidentes, tres en profundidad, causa raíz común y qué cambió.

Cinco fallos, la misma forma: **degradan bien y no dejan rastro**.

| dónde | qué hacía |
|---|---|
| `ingest.main()` | `return 0` incondicional: un cambio de layout del PDF dejaba la tarea diaria en verde |
| `/api/consulta` | `catch` vacíos: "sin clave" y "clave inválida" daban el mismo 200 |
| `_add_verificacion` | `ModuleNotFoundError` capturado en silencio; el sitio sin insignia y nada en rojo |
| `cdk deploy` | salió con **código 0 sin desplegar**, por un bloqueo de `cdk.out` |
| CI y cron | fallan desde hace días por facturación, sin que nadie mire |

Ninguno lo delató una alarma. A cuatro los delató un número que estaba en la
salida porque alguien lo puso ahí: `chunks_totales: 9280` cuando debía ser 9281,
`verificacionError` en una clave que casi nadie mira, `sisap_disponible: false`.

Y el más caro no fue el que rompía, sino el que **respondía con seguridad algo
falso**: a "¿cómo va el pollo vivo?" el asistente contestaba que no lo seguíamos,
con el precio en el mismo JSON y pintado en el dashboard. Una negación afirmada
como hecho es peor que un "no sé", porque el usuario no tiene forma de dudarla.

Era, además, un empeoramiento **causado por mejorar**: el peldaño de catálogo sí
incluía los otros mercados, pero el peldaño RAG no, y el RAG gana y nunca le cede
el turno.

---

## 6. Techos medidos

| techo | hoy | dónde revienta |
|---|---|---|
| Snapshot en memoria | 3,5 MB, 150 ms de parseo | **~15 MB** — por encima hay que migrar a acceso por clave |
| Estado de Step Functions | 36,6 KB de 256 KB | **510 productos** (514 bytes por parte del Map) |
| Timeout del trabajador | 39 s de 300 | ~7,6× más historia |
| Concurrencia de la cuenta | 8 de 10 | ya se roza; se pide por Service Quotas |
| Cuota de Jina | 25.428 tokens/corrida (0,50%) | ~9 meses al ritmo actual |
| Cuota de Titan (si se enciende) | — | **60 req/min, no ajustable** |

Y el punto donde escalar deja de ser gratis: con **×100 productos** el `Map`
costaría ~246 USD/mes de Lambda. El culpable tiene nombre — re-ajustar un GBM en
cada origen del walk-forward es caro por diseño — y la palanca no es de
infraestructura, es decidir si el kill-gate necesita correr a diario.

---

## 7. Vigilancia: quién mira qué

Dos instrumentos que no se solapan.

| qué vigila | dónde | cómo |
|---|---|---|
| Que la tubería no falle | CloudWatch → SNS | `ExecutionsFailed`, `ExecutionsTimedOut` |
| Que el dato no envejezca | CI, a diario | *Dead-man's-switch* sobre el snapshot publicado |

**No hay alarma de "la tubería no corrió", y es deliberado.** Alarmar sobre
ausencia exige una ventana, y aquí la ventana honesta es enorme: la máquina corre
de lunes a viernes, así que entre el viernes y el lunes pasan 72 horas
legítimas. Con ventana corta sonaría cada sábado, y una alarma que suena cuando
no pasa nada deja de mirarse — el mismo error que ya se cometió con el guard del
README y se corrigió.

Esa vigilancia la hace el dead-man's-switch, y midiendo el **síntoma** (el dato
está viejo) en vez de la **causa** (la máquina no arrancó), que además cubre el
caso de que corra en verde y publique basura.

`treat_missing_data = NOT_BREACHING`: la mayor parte del día no hay ejecuciones y
por tanto no hay dato; sin eso la alarma viviría en `INSUFFICIENT_DATA`.

Verificado que el cableado dispara de verdad, no solo que exista:

```
aws cloudwatch set-alarm-state --state-value ALARM ...
→ Action  Successfully executed action arn:aws:sns:...:PrecioVivoAlarmas-Alertas...
```

Las alarmas viven en su **propio stack**: si mañana se rehace la Fase 3,
`cdk destroy PrecioVivoFase3` se llevaría las alarmas justo cuando más falta
hacen. Coste: 0,00 USD (CloudWatch regala 10 alarmas, SNS 1.000 correos/mes).

**Falta suscribir un buzón.** Es lo único que no se puede automatizar: AWS exige
confirmar la suscripción desde el propio correo.

```bash
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:813559109370:PrecioVivoAlarmas-Alertas03FB113A-9bR7KJDvoN2R   --protocol email --notification-endpoint tu@correo
```

---

## 8. Lo abierto, por orden de retorno

1. **Suscribir un correo al tema SNS** (comando arriba). Sin eso las alarmas
   disparan al vacío.
2. **Rotar las tres claves.**
3. **Resolver el acceso a SISAP desde AWS** —otra región, un proxy peruano— o
   aceptar el portátil como dependencia declarada.
4. **Bedrock**, que está escrito, probado y deliberadamente apagado: no tiene
   capa gratuita y el requisito vigente es gasto cero.

### Nota sobre la primera corrida autónoma

El programador disparó puntual el 2026-08-19 a las 08:00 de Lima, recorrió la
tubería entera y falló en `PublicarEnGitHub` porque el parámetro
`/preciovivo/github-token` todavía no existía. El error decía el comando exacto
para crearlo. Ya está resuelto; la siguiente corrida es la primera que debería
completarse sola de punta a punta.
