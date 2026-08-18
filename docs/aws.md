# Precio Vivo sobre AWS

Bitácora de infraestructura. Todo está definido en `infra/` (CDK en Python) y se
despliega con `cdk deploy`. Nada se creó desde la consola.

Cuenta `8135****70` · región `us-east-1`.

---

## Fase 0 — Bootstrap de CDK

CDK no puede desplegar en una cuenta virgen: necesita dónde subir los artefactos
y con qué rol crearlos. `cdk bootstrap` instala eso una vez por cuenta+región.

Stack: **`CDKToolkit`** — 12 recursos.

| recurso | para qué |
|---|---|
| `cdk-hnb659fds-assets-813559109370-us-east-1` (S3) | recibe el zip de la Lambda antes de desplegarlo |
| `cdk-hnb659fds-container-assets-…` (ECR) | imágenes de contenedor — **sin usar**, cuesta 0 |
| 5 roles IAM | deploy, file-publishing, image-publishing, lookup, cloudformation-execution |
| Parámetro SSM `/cdk-bootstrap/hnb659fds/version` | compatibilidad de versión |

**Excepción consciente a "IAM mínimo":** el rol `CloudFormationExecutionRole`
lleva `AdministratorAccess`. Es por diseño — tiene que poder crear cualquier
recurso que un stack declare. Se puede acotar con
`--cloudformation-execution-policies <arn>`; se dejó por defecto porque acotarlo
antes de saber qué recursos usa el proyecto produce fallos crípticos. Queda
anotado como deuda, no como descuido.

`cdk destroy` **no** lo borra. Ver "Cómo destruirlo todo".

---

## Fase 1 — El snapshot servido desde Lambda

Stack: **`PrecioVivoFase1`** — 13 recursos.

ARN: `arn:aws:cloudformation:us-east-1:813559109370:stack/PrecioVivoFase1/90bab6f0-9ac0-11f1-9a66-125180e78977`

### Qué se creó

| recurso | ARN / identificador |
|---|---|
| Bucket S3 | `arn:aws:s3:::preciovivofase1-snapshotbucketb2bf31d3-cg1401w9p7gp` |
| Función Lambda | `arn:aws:lambda:us-east-1:813559109370:function:PrecioVivoFase1-ConsultaFnD685A349-AShELCmInb0P` |
| Rol de ejecución | `arn:aws:iam::813559109370:role/PrecioVivoFase1-ConsultaFnServiceRole36B1DB6A-Qvkj9ryaVdW4` |
| Function URL | `https://kus253gecbhyo6wvkhh5iqhmfa0ltjsu.lambda-url.us-east-1.on.aws/` |
| Grupo de logs | `PrecioVivoFase1-ConsultaFnLogs391C4AC0-CgkOihONxDln` (retención 7 días) |

Tres recursos más (`Custom::S3AutoDeleteObjects` más su Lambda y su rol) existen
solo para que `cdk destroy` pueda vaciar el bucket: S3 se niega a borrar buckets
con objetos dentro.

### Endpoints

| ruta | patrón de acceso | por qué éste |
|---|---|---|
| `GET /productos/{slug}` | puntual por clave | el `GetItem` de manual |
| `GET /productos/{slug}/serie?desde&hasta` | rango sobre clave de ordenamiento | el `PK + SK BETWEEN` de DynamoDB |
| `GET /comparar?productos=a,b&dias=30` | dispersión y recolección | se convierte en `BatchGetItem` |
| `GET /health` | metadatos del dataset | sirve para provocar un arranque en frío |

No se eligieron por bonitos: **cubren los tres patrones de acceso que una tabla
de DynamoDB tendría que servir** si el snapshot dejara de caber en memoria. Eso
es exactamente lo que la Fase 2 tiene que modelar.

Los cuatro delegan en `service.py`. El handler no tiene lógica de negocio: si un
endpoint necesitara reglas propias, estaría mal ubicado.

### La decisión central: carga fuera del handler

El snapshot se descarga y se parsea en el **ámbito del módulo**. Lambda ejecuta
el módulo una vez por contenedor y congela el proceso entre invocaciones.

**Medido en CloudWatch** sobre 25 invocaciones reales:

| | |
|---|---|
| `Init Duration` (frío: descarga S3 + parseo de 3,3 MB) | **707,5 ms** |
| `Duration` p50 (caliente) | **1,78 ms** |
| `Duration` p95 | **2,35 ms** |
| `Max Memory Used` | 123 MB de 512 |
| ratio | **397×** |

El efecto en costo es el argumento de verdad: **multiplicar el tráfico por 100
no multiplica por 100 las lecturas a S3**, porque se lee una vez por contenedor
y no una vez por petición.

Lo que este diseño **no** garantiza: "caliente" no es un estado permanente. AWS
recicla contenedores cuando quiere, así que la proporción de arranques en frío
es una propiedad estadística del tráfico — y por eso la Fase 2 la mide.

**512 MB de memoria** no es por RAM (se usan 123): en Lambda **la CPU escala con
la memoria**, y el arranque en frío está dominado por parsear JSON, que es CPU
pura.

### Empaquetado: ni capa ni contenedor

Se midió antes de decidir. Importar `service.py` y ejecutar los endpoints de
lectura **no carga ningún paquete de terceros**: solo stdlib.

```
zip desplegado: 65 KB   (handler + __init__ + service + corpus)
límite:         250 MB descomprimido
```

No hace falta capa ni imagen de contenedor. Esto no es suerte: `service.py` lee
del snapshot, no de la base, y las dependencias pesadas (`numpy`,
`scikit-learn`, `pdfplumber`) viven en el *pipeline*, no en el *transporte*.

**Cuándo cambia:** el endpoint `/consulta` (RAG) necesita `numpy` (~30 MB). Ahí
la recomendación sería **capa**, no contenedor: se comparte entre funciones y no
obliga a mantener un repositorio ECR.

### Permisos IAM

Política en línea del rol de la función:

```
Allow  s3:GetObject   sobre   arn:aws:s3:::preciovivofase1-…/snapshot.json
```

Una acción, un objeto. **No** se usó `bucket.grant_read(fn)`: ese atajo concede
`s3:GetObject*`, `s3:GetBucket*` y `s3:List*` sobre el bucket entero. Funciona,
pero concede de más — si mañana se guardan ahí los artefactos del RAG, esta
función seguirá sin poder leerlos, y eso es lo correcto.

Política gestionada: `AWSLambdaBasicExecutionRole`, que permite escribir en
CloudWatch Logs. Sin ella la función no puede loguear nada.

### Lo que NO hay, y es deliberado

- **Sin VPC.** La función solo habla con S3, alcanzable por el endpoint público
  de AWS. Meterla en VPC obligaría a un NAT Gateway (~32 USD/mes) o a un
  endpoint de VPC, sin ganar nada: una Lambda fuera de VPC no expone puertos, y
  su superficie es el rol de IAM, acotado arriba a un solo objeto.
- **Sin API Gateway.** Aporta autorizadores, throttling por clave, dominios
  propios y WAF; nada se usa hoy, y cobra ~1 USD por millón de peticiones que la
  Function URL no cobra.
- **Sin capacidad aprovisionada.** Todo bajo demanda.

### Costo

Con unas 10.000 consultas al mes:

| concepto | mensual |
|---|---|
| Lambda (peticiones + GB-s) | 0,00 USD — capa gratuita permanente (1 M peticiones, 400.000 GB-s) |
| S3 almacenamiento (3,3 MB más versiones de ≤30 días) | ~0,01 USD |
| S3 GET (uno por arranque en frío) | ~0,00 USD |
| CloudWatch Logs (retención 7 días) | ~0,00 USD |
| Function URL | no cobra |
| **Total** | **≈ 0,01 USD** |

**Con ×100 (1 M consultas/mes):** ~0–2 USD. Lambda roza el borde de la capa
gratuita; S3 apenas se mueve, porque las lecturas van por contenedor y no por
petición. Lo que crecería es CloudWatch Logs, que sí escala con las invocaciones.

### El techo de este diseño, medido

El snapshot entero en memoria funciona *a esta escala*. Deja de funcionar así:

| escala | productos | snapshot | parseo en frío | RAM |
|---|---|---|---|---|
| hoy | 72 | 3,5 MB | 150 ms | 12 MB |
| ×4 | 288 | 13,9 MB | 0,7 s | 49 MB |
| ×14 | 1.008 | 48,5 MB | **2,4 s** | 172 MB |
| ×30 | 2.160 | 104 MB | **4,9 s** | 368 MB |

**Umbral: por encima de unos 15 MB de snapshot hay que migrar.** No antes — a
3,3 MB, cualquier base de datos añadiría latencia de red a una consulta que hoy
tarda 1,78 ms.

Las tres salidas cuando llegue el momento:

1. **DynamoDB** con `PK=producto`, `SK=fecha`. La consulta pasa a ~5 ms pero deja
   de importar el tamaño total.
2. **S3 particionado por producto**: un objeto por producto (~50 KB), y la Lambda
   descarga solo lo que le piden. Casi gratis y sin base de datos.
3. **S3 Select / Athena**: suena elegante y suele ser peor — latencia alta y
   cobro por byte escaneado.

### Cómo destruirlo todo

```bash
cd infra && cdk destroy PrecioVivoFase3   # PRIMERO, ver abajo
cdk destroy PrecioVivoFase1
```

**El orden importa desde la Fase 3.** Ese stack importa este bucket vía *Exports*
de CloudFormation, así que destruir la Fase 1 primero falla — y falla bien:
CloudFormation impide borrar algo que otro stack está usando.

Destruir la Fase 1 borra los 13 recursos, **incluido el bucket con su contenido**
(de eso se ocupa `auto_delete_objects`), y con él se va también el estado de la
Fase 3, que vive bajo el prefijo `estado/`.

**Queda huérfano el bootstrap.** Para no dejar rastro:

```bash
# 1. vaciar el bucket de artefactos (tiene versionado)
aws s3 rm s3://cdk-hnb659fds-assets-813559109370-us-east-1 --recursive
# 2. borrar el stack del bootstrap
aws cloudformation delete-stack --stack-name CDKToolkit
```

El repositorio ECR del bootstrap está vacío y no cobra, pero se va con el stack.

### Tres preguntas de entrevista

**1. Cargas el snapshot fuera del handler. ¿Qué pasa si el snapshot cambia
mientras el contenedor sigue vivo?**

Sirve datos viejos hasta que AWS recicle el contenedor, y eso puede tardar
minutos u horas. Es una decisión, no un descuido: el snapshot se reescribe una
vez por día hábil y son precios de cierre, así que unos minutos de desfase no
cambian ninguna respuesta. Si hiciera falta acotarlo, hay tres opciones por
orden de coste. La primera, comprobar el `ETag` con un `HeadObject` cuando el
dato en memoria supere cierta antigüedad: es una llamada barata que no descarga
el objeto. La segunda, publicar el snapshot con una clave versionada y desplegar
la función con esa clave en una variable de entorno, de modo que un snapshot
nuevo implique una versión nueva de la función y por tanto contenedores nuevos.
La tercera, renunciar a la caché y leer en cada petición, que cuesta 700 ms cada
vez. Elegiría la primera.

**2. ¿Por qué Function URL y no API Gateway?**

Porque no uso nada de lo que API Gateway añade: autorizadores, throttling por
clave de API, dominio propio, WAF, transformaciones de petición. Cobra alrededor
de 1 USD por millón de peticiones que la Function URL no cobra, y añade un salto
más de latencia. Migraría en cuanto necesitase dominio propio, WAF o cuota por
cliente. Conviene saber que Function URL sí soporta CORS y autenticación IAM con
SigV4, que cubre bastante más de lo que suele suponerse.

**3. Tu Lambda no está en una VPC. ¿No es un problema de seguridad?**

No, y meterla en una VPC sería peor, por dos razones. La superficie de ataque de
una Lambda no es la red: no expone puertos ni escucha nada. Su superficie es el
rol de IAM, y ese rol permite exactamente una acción (`s3:GetObject`) sobre
exactamente un objeto. Además, una Lambda en VPC pierde el acceso a internet y a
los endpoints públicos de AWS; para recuperar S3 haría falta un NAT Gateway
(~32 USD/mes) o un endpoint de VPC de tipo gateway. Se mete en VPC cuando hay
que alcanzar algo que *solo* vive en la red privada: una RDS, un Redis, un
servicio interno. Aquí no hay nada de eso.

---

## Fase 2 — Telemetría de consultas en DynamoDB

Añadido al stack `PrecioVivoFase1`.

| recurso | ARN |
|---|---|
| Tabla DynamoDB | `arn:aws:dynamodb:us-east-1:813559109370:table/PrecioVivoFase1-TelemetriaTable426E2E76-11MGJC3Z7KKKY` |

Modo `PAY_PER_REQUEST`. TTL sobre el atributo `ttl`, 30 días.

### Primero: ¿hacía falta la tabla?

CloudWatch **ya** respondía las dos preguntas. El handler de la Fase 1 emite
métricas EMF (`LatenciaMs`, `ColdStart`) y de ahí salen el p95 y el porcentaje
de arranques en frío sin ninguna tabla.

Lo que DynamoDB aporta es la **cardinalidad**. CloudWatch cobra por serie
temporal única:

| dimensión | series | coste |
|---|---|---|
| `endpoint` (3 valores) | 6 | 1,80 USD/mes |
| `producto` (72 valores) | 148 | **44,40 USD/mes** |
| `producto` con 1.008 productos | 2.020 | **606 USD/mes** |

La regla: **baja cardinalidad a dimensión de CloudWatch; alta cardinalidad a
campo de un registro crudo en DynamoDB.** No compiten, se reparten el trabajo.
CloudWatch da agregados en caliente; DynamoDB permite re-agregar mañana por una
pregunta que hoy no existe.

**Un bug que esto destapó:** la Fase 1 emitía `Endpoint=/productos/papa-blanca`
— el slug dentro de la dimensión. Una serie temporal por producto. Corregido
aquí normalizando a la plantilla `/productos/{slug}`; el producto concreto pasó
a ser un campo del log, no una dimensión.

### El modelado de claves

```
PK = endpoint#fecha     "/comparar#2026-08-18"
SK = timestamp#id       "2026-08-18T05:20:17.564Z#979bb561"
```

**Lo primero que hay que entender de DynamoDB: la clave de partición solo admite
igualdad exacta.** No existe el rango sobre PK. Solo la clave de ordenamiento
acepta `BETWEEN`, `>`, `begins_with`.

Alternativas y por qué se descartaron:

| candidata | veredicto |
|---|---|
| `PK = timestamp` | «lo de las últimas 24 h» sería imposible sin `Scan`, que lee la tabla entera y cobra por ello |
| `PK = producto` | responde «historial del tomate», que no es ninguna de las dos preguntas |
| `PK = fecha`, `SK = endpoint#timestamp#id` | **la mejor por consultas**: «todo el día D» en una Query, y «el endpoint X del día D» también con `begins_with`. Descartada porque concentra todas las escrituras del día en una partición, y el tope es 1.000 escrituras/segundo |

**Honestidad sobre esa última.** A 10.000 peticiones/mes se escriben 0,004 por
segundo: estamos a **250.000× del límite**. Es decir, la elección es correcta
por una razón que hoy no aplica. Lo que la justifica es que sigue siendo
correcta cuando el volumen suba, sin migrar nada — y que saber qué se está
optimizando y a qué costo vale más que la optimización.

**El costo de la elección, que hay que decir:** el p95 global necesita **una
Query por endpoint**. Con 3 endpoints son 3 consultas y se fusionan en memoria.
Con 30 endpoints serían 30, y ahí la alternativa descartada se volvería la
buena. El diseño de claves no es «correcto» en abstracto: es correcto para un
número de endpoints y un volumen de escritura concretos.

El `#id` del SK no es adorno: dos llamadas en el mismo milisegundo tendrían la
misma clave y una sobrescribiría a la otra.

`frio` se guarda como `0/1` y no como booleano, para que **el promedio de la
columna sea directamente la proporción de arranques en frío**, sin transformar
nada al leer.

**Sin GSI.** Un índice por producto duplicaría las escrituras y todavía no
existe la consulta que lo justifique. El índice se añade cuando aparece la
pregunta, no antes.

### Lo que cuesta la propia telemetría

Se escribe de forma **síncrona** en la ruta de la petición, con un interruptor
(`TELEMETRIA=0/1`) puesto precisamente para poder medirlo. Instrumentado desde
dentro de la función:

| | p50 | p95 |
|---|---|---|
| ruteo (`service.py`) | 0,187 ms | 0,218 ms |
| **escritura a DynamoDB** | **4,901 ms** | 5,483 ms |
| `Duration` total | 7,020 ms | 7,940 ms |

**La telemetría es el 70 % de la latencia total.** La p50 pasó de **1,73 ms**
(Fase 1, sin telemetría) a **7,02 ms**.

Se acepta a propósito: a este volumen, 5 ms no le importan a nadie, y la
alternativa desacoplada (log EMF → *subscription filter* → segunda Lambda que
escribe) añade dos piezas más que hoy no aportan.

**Nota metodológica, porque la primera medición fue mala.** El primer A/B daba
«coste ≈ 0», y era falso: se midió justo después de un
`update-function-configuration`, con contenedores recién creados, y el ruido de
las primeras invocaciones (7 ms) tapaba la señal. La medición buena exige o bien
calentar de verdad antes de medir, o bien instrumentar desde dentro. Se hizo lo
segundo, que es lo único que separa el coste de la escritura del ruido del
runtime.

### Las dos preguntas, respondidas con datos propios

```
endpoint                     n      p50      p95   % frio
/productos/{slug}          200   0.185ms   0.212ms     1.5%
/comparar                   50   0.515ms   0.555ms     0.0%
GLOBAL                     250   0.186ms   0.524ms     1.2%
```

Esas latencias son las del **ruteo**, no las de la respuesta completa: se miden
antes de escribir la telemetría, a propósito. Si se midieran después, la métrica
estaría midiendo su propio coste.

El **1,2 % de arranques en frío** es la cifra que justifica cargar el snapshot
fuera del handler: 98,8 % de las peticiones no pagan los 707 ms.

### Permisos IAM añadidos

```
Allow  dynamodb:PutItem   sobre   arn:aws:dynamodb:…:table/PrecioVivoFase1-TelemetriaTable…
```

Solo `PutItem`. La función **escribe** telemetría y nunca la lee: las consultas
de análisis las hace un operador con sus propias credenciales. Sin `Query`, sin
`Scan`, sin `DeleteItem`, sin `UpdateItem`.

Igual que con S3, se evitó el atajo `tabla.grant_write_data(fn)`, que además
concede `BatchWriteItem`, `UpdateItem` y `DeleteItem`.

### Costo

| concepto | mensual |
|---|---|
| DynamoDB escrituras bajo demanda (10.000/mes) | 0,00 USD — capa gratuita: 25 GB, 2,5 M lecturas, 1 M escrituras |
| Almacenamiento (10.000 × ~200 B, TTL 30 días) | ~0,00 USD |
| Borrados por TTL | **gratis** |
| **Total añadido** | **≈ 0,00 USD** |

**Con ×100 (1 M escrituras/mes):** ~1,25 USD/mes en escrituras. El
almacenamiento sigue acotado porque el TTL poda a 30 días.

### Cómo destruirlo

Va dentro del mismo `cdk destroy PrecioVivoFase1` — la tabla tiene
`RemovalPolicy.DESTROY`. En producción sería `RETAIN`.

### Tres preguntas de entrevista

**1. ¿Por qué `PK = endpoint#fecha` y no `PK = fecha`?**

Con `PK = fecha` respondo más preguntas con menos consultas: todo el día en una
Query, y un endpoint concreto con `begins_with` sobre la clave de ordenamiento.
Es mejor de leer. La descarté porque concentra todas las escrituras del día en
una sola partición, y DynamoDB tiene un tope de 1.000 escrituras por segundo por
partición. Ahora bien, siendo honesto: yo escribo 0,004 por segundo, así que
estoy a 250.000 veces del límite y la razón hoy no me aplica. Elegí la que sigue
siendo correcta cuando el volumen suba, porque migrar un esquema de claves
implica reescribir la tabla. El costo de mi elección es que el p95 global me
cuesta una Query por endpoint: con tres es trivial, con treinta cambiaría de
diseño.

**2. Guardas la telemetría de forma síncrona en la ruta de la petición. ¿No es
eso un problema?**

Sí, y lo medí: la escritura son 4,9 ms sobre un ruteo de 0,19 ms, o sea el 70 %
de la latencia total. La p50 pasó de 1,73 a 7,02 ms. Lo acepté porque a mi
volumen 5 ms no le importan a nadie y porque dejé un interruptor para poder
medirlo. La alternativa es desacoplar: el handler solo escribe el log, un
subscription filter de CloudWatch Logs dispara una segunda Lambda y esa escribe
en DynamoDB. La latencia de respuesta queda intacta a cambio de dos piezas más
que mantener. Lo haría si la latencia importara o si el volumen subiera lo
bastante como para que las escrituras costaran dinero.

**3. Si CloudWatch ya te da p95 y porcentaje de arranques en frío, ¿para qué la
tabla?**

Para la cardinalidad. CloudWatch cobra por serie temporal única, así que meter
el producto como dimensión cuesta 44 USD al mes con 72 productos y 606 con mil.
DynamoDB guarda el registro crudo por céntimos y me deja re-agregar después por
cualquier campo, incluso por preguntas que hoy no existen. La regla que sigo es:
baja cardinalidad a dimensión de CloudWatch, alta cardinalidad a campo de un
registro en DynamoDB. De hecho esto me hizo encontrar un bug: en la fase
anterior estaba emitiendo el slug del producto dentro de la dimensión, que es
exactamente el error que acabo de describir.

---

## Fase 3 — La ingesta diaria, orquestada

Stack: **`PrecioVivoFase3`** — 27 recursos.

ARN: `arn:aws:cloudformation:us-east-1:813559109370:stack/PrecioVivoFase3/c279d8d0-9b45-11f1-8a8a-12f344fd0607`

### Qué se creó

| recurso | ARN / identificador |
|---|---|
| Máquina de estados | `arn:aws:states:us-east-1:813559109370:stateMachine:Diaria5ADE8804-twQvVXkfjRsW` |
| Programador | `arn:aws:scheduler:us-east-1:813559109370:schedule/default/PrecioVivoFase3-Diario-CSFCXGQHR3N7` |
| Lambda cosecha | `PrecioVivoFase3-IngestaD47E65D5-27579CHIFJoV` |
| Lambda listado | `PrecioVivoFase3-ForecastListar126FE65F-P6fAKCGt1LY5` |
| Lambda por producto | `PrecioVivoFase3-ForecastProducto01E0F8AF-7cjPi8vtZdI8` |
| Lambda reducción | `PrecioVivoFase3-ForecastReducirC4668B38-d1HKQnDczGII` |
| Lambda export | `PrecioVivoFase3-Export16632B07-1gTxH2x9vXtV` |

Más 5 grupos de logs, 7 roles y sus políticas.

**Sin bucket nuevo.** El estado vive bajo el prefijo `estado/` del bucket de la
Fase 1, porque el export tiene que escribir `snapshot.json` ahí de todos modos:
el acoplamiento entre fases ya existía y duplicar el bucket solo lo escondería.
La contrapartida está en "Cómo destruirlo".

```
estado/preciovivo.db          7,6 MB   la SQLite entera
estado/forecast_cache.json   26,8 KB   lo que calcula el forecast, lo lee el export
snapshot.json                 3,3 MB   lo que sirve la Fase 1
```

### Lo primero que había que averiguar: ¿deja el WAF entrar a Lambda?

`harvester.py` avisa en su propio docstring de que corre en local *porque el WAF
de gob.pe no bloquea esa máquina*, y el script diario advierte de que un runner
de datacenter podría estar bloqueado. gob.pe sirve tras **Huawei Cloud WAF**
(cookie `HWWAFSESID`) y Lambda sale por IP de datacenter de AWS. Eso es un
go/no-go de la fase entera y no se puede responder desde casa.

Se desplegó un stack desechable con una Lambda que recorre la cadena real del
harvester —no una reimplementación—, se invocó una vez y se destruyó. El código
sigue en `infra/lambda_sonda/`, porque la forma de contestar la pregunta vale
tanto como la respuesta.

| paso | resultado |
|---|---|
| IP de salida | `3.215.153.209` (AWS us-east-1) |
| Colección 335 | 155.255 bytes, **83 páginas de mes parseadas** |
| Listado de PDFs | 2 fechas, la última la de hoy |
| Descarga del CDN | **612.162 bytes**, magic `%PDF-` válido |

No se comprobó "status 200": un WAF devuelve 200 con una página de desafío tan
contento, y el código diría que funciona sin funcionar. Se comprobó que el
contenido **sirve para cosechar**.

Dato secundario que sí cambió el diseño: la colección tarda **15 s desde Lambda**
contra 1,4 s desde una máquina en Lima. No es bloqueo, son ~3 s por petición. Los
timeouts se dimensionaron con eso.

### La máquina de estados

```
Cosechar → PasoLaCompuerta ─┬→ CompuertaDeEscritura (Fail)
                            └→ ListarProductos → PorProducto (Map ×8) → Reducir → Exportar → Publicado
```

**Step Functions Standard y no Express**: Express corta a los 5 minutos y esto
dura ~7. Standard además guarda el historial completo de cada ejecución 90 días
sin configurar nada — que es la razón de que **no** se active el registro en
CloudWatch Logs: duplicaría el mismo dato y sí cobraría por él.

**EventBridge Scheduler y no una regla de EventBridge**: entiende
`America/Lima` de forma nativa. No hay una resta a UTC escrita a mano que alguien
tenga que revisar cuando cambie algo.

```
cron(0 8 * * ? *)   ScheduleExpressionTimezone: America/Lima   FlexibleTimeWindow: OFF
```

Todos los días, también fin de semana. Un día sin reporte nuevo es un no-op que
termina en verde, y `--latest 5` hace que la tubería **se cure sola**: si un día
falla, el siguiente recupera lo que faltaba. Programarla solo de lunes a viernes
ahorraría dos ejecuciones vacías al mes y perdería esa propiedad.

### La política de reintentos, que es lo que separa una tubería de un cron

No es la misma para todos los pasos, y ésa es la idea:

| paso | reintentos | por qué |
|---|---|---|
| cosecha, listado, export | `States.ALL` ×3, backoff ×2 | fallan por red o por S3, que son transitorios — y se pueden reintentar **porque son idempotentes**: `upsert_precios` reescribe lo mismo y el snapshot se regenera entero. Si no lo fueran, el reintento sería el bug |
| trabajadores del forecast | **solo errores de infraestructura de Lambda** | un cálculo determinista que falló volverá a fallar; reintentarlo es pagar para llegar al mismo sitio más tarde |
| el parseo | **nunca** | y no por política, sino por estructura |

Lo del parseo merece el detalle: un PDF que no se parsea **no lanza excepción**.
Sale como `bloqueados` en el resultado y la máquina deriva a un estado `Fail`. No
hay reintento posible porque no hay error que reintentar.

### El fallo visible, que era un agujero real

`ingest.main()` termina con `return 0` pase lo que pase. La compuerta `MIN_ROWS`
hace bien su trabajo —bloquea la carga de un reporte estructuralmente roto— pero
después el proceso sale con éxito. Si mañana MIDAGRI cambia el layout del PDF, la
tarea diaria de Windows queda **en verde** mientras deja de entrar el dato.

Una compuerta que protege el dato y no avisa a nadie no es una compuerta.

Aquí la condición es explícita y está elegida con cuidado:

```python
fallo_de_compuerta = bool(objetivos) and cargados == 0 and bloqueados > 0
```

"Cero cargados" **no** es fallo por sí solo: un sábado no hay reporte nuevo y no
cargar nada es la respuesta correcta. Marcarlo como error entrenaría a cualquiera
a ignorar las alarmas, que es peor que no tenerlas. El fallo de verdad es haber
**encontrado** reportes y que **todos** quedaran bloqueados: los PDFs están ahí y
ya no sabemos leerlos.

### El forecast: por qué está repartido

Medido sobre la BD real antes de decidir nada:

| bloque | tiempo | ¿paralelizable? |
|---|---|---|
| `forecast_producto` (h=1), 73 productos | 619,8 s | sí, es puro por producto |
| `_comparacion_modelos` | 629,1 s | por dentro sí; por fuera agrega |
| `_forecast_7d` | mismo orden | sí |
| `_kill_gate` | **0,5 s** | irrelevante |

El comentario de `actualizar-diario.ps1` dice `~14 min`. **Está desactualizado**:
el walk-forward crece con la historia y hoy son ~31 min en esa máquina. Contra el
límite duro de Lambda, 15 minutos, no cabe por el doble.

**El refactor, y lo que NO se tocó.** `_comparacion_modelos` era
`for h: for producto: <cuatro MAE>` con una media al final. Se extrajo el cuerpo
del bucle a `_comparacion_producto(series, h)`. `_walk_forward_mae`,
`_walk_forward_mae_lineal`, `_walk_forward_mae_gbm`, `_impute_vol` y `_limpiar`
se llaman con los mismos argumentos y en el mismo orden; el re-ajuste del GBM por
origen y el forward-fill causal del volumen siguen donde estaban.

Verificado como debe verificarse un refactor de la pieza más delicada del repo:
capturando la salida completa antes y después sobre los 73 productos reales.

```
antes     1708 bytes  sha256 d259fb94189785e8
despues   1708 bytes  sha256 d259fb94189785e8
bytes identicos: True
```

### La ejecución real, medida

Ejecución `verificacion-manual-3`, **SUCCEEDED en 397 s**:

| paso | resultado |
|---|---|
| Cosechar | 5 días cargados, 0 bloqueados, 37.892 filas, última fecha 2026-08-18 — **19 s** |
| Map (73 productos) | p50 **39,17 s**, p95 44,92 s, máx 48,21 s · 11 contenedores · 10 arranques en frío de 83 invocaciones (init 1,73 s) · **333 MB usados de 1.769** |
| Reducir | 73 pronósticos guardados, caché de 27.448 bytes — **2,3 s** |
| Exportar | snapshot de 3.500.806 bytes, 72 productos, **72 con forecast** — **4,1 s** |

**Corrección honesta:** yo había estimado ~25 s por producto y ~4 min de reloj.
Ese número salía de mi máquina; la vCPU de Lambda es más lenta. Lo real son
**39 s** por producto y 397 s de reloj — y en serie serían **52 minutos**, no 31.
El argumento a favor de repartirlo sale reforzado, pero la estimación era mía y
estaba mal.

Los 333 MB usados de 1.769 no son un error de dimensionamiento: la memoria se
eligió por **CPU**, no por RAM. Lambda asigna una vCPU completa alrededor de los
1.769 MB, y el GBM es CPU pura de un solo hilo. La medición confirma que la RAM
nunca fue el criterio.

**El círculo se cierra**: el export escribe en el mismo `snapshot.json` que lee la
Lambda de la Fase 1, y `/health` respondió con `ultima_fecha: 2026-08-18` y
`generado_en: 2026-08-18T21:05:32+00:00` minutos después. Las dos fases dejaron
de ser dos demos.

Un resultado que no es de infraestructura pero conviene anotar: con 537 fechas de
historia el GBM ya se evalúa en los 72 productos (`gbm_disponible: true`) y
**pierde contra AR(1)** en ambos horizontes (0,1711 contra 0,1476 a un día). El
kill-gate está diciendo exactamente lo que se construyó para decir.

### Empaquetado: tres paquetes, no uno

| Lambda | dependencias | tamaño |
|---|---|---|
| cosecha | `requests` + `pdfplumber` | ~60 MB |
| export | `numpy` + `holidays` | ~59 MB |
| forecast | + `scikit-learn` (arrastra scipy) | **207,6 MB** |

El límite, leído de la API y no de memoria:

```
aws lambda get-account-settings → CodeSizeUnzipped: 262144000   (250 MiB)
```

**El export no lleva scikit-learn y no es un descuido.** `export._add_forecast`
llama a `forecast_all_cached`, así que importa `forecast`, que importa numpy. Pero
sklearn lo importa `forecast` **dentro de `_nuevo_gbm`**, no en el módulo: con el
caché ya escrito por el paso anterior ese camino nunca se recorre. Un import
perezoso escrito por otra razón acaba decidiendo el empaquetado. Por eso se mide.

**Se construye en Docker**, con la imagen de build de SAM. No por gusto: `pip
install --target` desde este Windows con Python 3.14 deja el bytecode como
`.cpython-314.pyc`, inútil para el runtime 3.13 — 33 MB de peso muerto y ninguna
precompilación válida, así que cada arranque en frío recompilaría.

**Ni capa ni imagen de contenedor.** En crudo el forecast pesa 275 MB y no cabe;
podando `tests/` baja a 207,6 con 42 MB de margen. Un zip basta, y eso ahorra un
repositorio ECR.

### Tres errores propios, porque también son parte del resultado

**1. `cp -a` en el bundling.** El ejemplo de la documentación de CDK usa
`cp -au . /asset-output`. En un bind mount de Docker Desktop sobre Windows,
corriendo como UID 1000, falla con `preserving times: Operation not permitted` y
tumba el bundling entero. Se cambió por `cp -r`.

**2. Podar los `.dist-info`.** Ahorraban 1 MB de 208 y rompieron la ejecución
real: `Unable to import module 'forecast_lambda': No package metadata was found
for holidays`. `holidays` lee su propia versión con `importlib.metadata`, que
resuelve contra ese directorio. Los `.dist-info` no son metadatos muertos.

**3. `max_concurrency=20` contra una cuota de 10.** La primera corrida con el
Map murió con `Lambda.TooManyRequestsException`. La causa, medida:

```
aws lambda get-account-settings → ConcurrentExecutions: 10
```

Una cuenta nueva de AWS arranca con **10 ejecuciones concurrentes**, no con las
1.000 que suelen suponerse. Se bajó a 8, dejando 2 para la Lambda de consultas de
la Fase 1, que comparte el mismo cupo. Se sube pidiendo cuota (Service Quotas,
`L-B99A9384`); no se pide desde el código, porque cambiar los límites de una
cuenta no es algo que deba hacer la infraestructura por su cuenta.

Los tres los encontró **correr la tubería de verdad**, no sintetizar la plantilla.

### Permisos IAM

Una sentencia por necesidad real. Ninguna función recibe el bucket entero:
`grant_read`/`grant_write` de CDK conceden familias completas (`GetObject*`,
`GetBucket*`, `List*`) y aquí se sabe exactamente qué objeto toca cada paso.

| función | permiso | por qué |
|---|---|---|
| cosecha | `s3:GetObject`, `s3:PutObject` sobre `estado/preciovivo.db` | lee la BD y la reescribe |
| listado, por-producto | `s3:GetObject` sobre `estado/preciovivo.db` | **solo leen**. 73 escritores concurrentes sobre el mismo objeto es justo el fallo que no debe ser posible |
| reducción | `Get`+`Put` sobre `estado/preciovivo.db` y `estado/forecast_cache.json` | escribe el caché y el historial de pronósticos |
| export | `Get` sobre los dos de `estado/`, `Put` sobre `snapshot.json` | lee el estado, publica el snapshot |
| máquina de estados | `lambda:InvokeFunction` sobre las 5 funciones | |
| programador | `states:StartExecution` sobre esta máquina | |

Ninguna función necesita permisos de red: salir a internet no lo concede IAM, lo
concede estar fuera de una VPC.

### Escritura condicional: por qué no basta un PutObject

**EventBridge Scheduler entrega al menos una vez.** Es el contrato del servicio,
no una hipótesis de manual. Dos ejecuciones solapadas descargarían la misma
SQLite, cada una haría su trabajo, y la última en subir borraría el trabajo de la
otra **sin dejar un solo error en ningún log**. La pérdida silenciosa es el peor
fallo posible porque nadie la busca.

`IfMatch` con el ETag que se leyó convierte eso en un 412: la segunda ejecución
falla ruidosamente y la máquina queda en rojo. En la primera ejecución, cuando el
objeto aún no existe, se usa `IfNoneMatch: "*"`. Cuesta cero.

### Costo

Por corrida: 73 × 39,17 s × 1,769 GB ≈ 5.058 GB-s del Map, más ~30 de los otros
cuatro pasos. Con 30 corridas al mes son **~152.700 GB-s**.

| concepto | mensual |
|---|---|
| Lambda | **0,00 USD** — capa gratuita permanente de 400.000 GB-s |
| Step Functions Standard (~4.560 transiciones) | ~0,01 USD — 4.000 gratis |
| S3 (versiones de la BD, expiran a 30 días) | ~0,01 USD |
| EventBridge Scheduler (30 disparos) | 0,00 USD — 14 M gratis |
| **Total** | **≈ 0,02 USD** |

**Con ×100 productos (7.300) esto deja de ser gratis.** El Map serían 15,2 M
GB-s/mes: **~246 USD/mes** de Lambda más ~11 de Step Functions. Es el primer
sitio del proyecto donde escalar cuesta dinero de verdad, y el culpable tiene
nombre: re-ajustar un GBM en cada origen del walk-forward es caro por diseño. Ahí
la palanca no es de infraestructura —es decidir si el kill-gate necesita correr
todos los días o basta una vez por semana.

### Tres techos, medidos

| techo | hoy | dónde revienta |
|---|---|---|
| Estado de Step Functions | 36,6 KB de 256 KB (14%) | **510 productos**: cada parte del Map son 514 bytes. Al pasarlo, cada trabajador escribe su resultado en S3 y el reductor los lee |
| Timeout del trabajador | 39 s de 300 | **~7,6× más historia** por producto |
| Concurrencia de la cuenta | 8 de 10 | ya lo estamos rozando: el Map es lo que limita la cuota, no el diseño |

### Cómo destruirlo

```bash
cd infra && cdk destroy PrecioVivoFase3   # primero éste
cdk destroy PrecioVivoFase1               # después éste
```

**El orden importa.** La Fase 3 importa el bucket de la Fase 1 vía *Exports* de
CloudFormation, así que destruir la Fase 1 primero falla. Es el precio de
reutilizar el bucket en vez de crear uno nuevo, y es el comportamiento correcto:
CloudFormation impide borrar algo que otro stack está usando.

Destruir la Fase 3 **no** borra `estado/preciovivo.db` ni `estado/forecast_cache.json`:
viven en el bucket de la Fase 1 y se van con él.

El bootstrap sigue quedando huérfano; ver "Cómo destruirlo todo" de la Fase 1.

### Tres preguntas de entrevista

**1. Tienes un cálculo de 52 minutos y Lambda corta a los 15. ¿Por qué no lo
sacaste a Fargate?**

Porque medí dónde estaba el tiempo antes de elegir servicio. De los 52 minutos,
medio segundo era agregación real y el resto era trabajo por producto,
independiente entre productos. Eso no es un trabajo largo: son 73 trabajos cortos
disfrazados de uno largo, y un `Map` de Step Functions los reparte a 39 segundos
cada uno. Fargate habría funcionado, pero necesita VPC, añade una pieza de
cómputo más que mantener, y sobre todo no arregla el problema de fondo: en serie
sigue creciendo con la historia, mientras que repartido el trabajo por unidad se
queda pequeño. El precio que pagué fue extraer el cuerpo de un bucle a una
función en el archivo más delicado del repositorio, así que lo verifiqué
capturando la salida completa antes y después sobre los datos reales: mismo
sha256, byte a byte.

**2. Los reintentos no son iguales en todos los pasos. ¿Por qué?**

Porque no fallan por lo mismo. La cosecha y el export fallan por red o por S3,
que son transitorios, y además son idempotentes —el upsert reescribe lo mismo y
el snapshot se regenera entero—, así que se pueden reintentar sin pensarlo. Si no
fueran idempotentes, el reintento sería el bug y no la solución. Los trabajadores
del forecast hacen un cálculo determinista: si falló, va a volver a fallar, así
que solo reintento errores de infraestructura de Lambda. Y el parseo no se
reintenta nunca, pero eso no lo consigue una política sino la estructura: un PDF
que no se parsea no lanza excepción, sale como bloqueado en el resultado y un
estado Choice deriva a un Fail. No hay error que reintentar.

**3. Guardas una SQLite en S3 y la bajas y subes entera. ¿Eso no es un problema?**

A este volumen, no: son 7,6 MB y hay un escritor al día. La alternativa es una
RDS, que cuesta unos 15 dólares al mes encendida a todas horas para servir una
escritura diaria, y arrastra VPC, y la VPC arrastra NAT Gateway o endpoints. Lo
que sí hice fue protegerlo del único escenario realista de corrupción: EventBridge
Scheduler entrega al menos una vez, así que dos ejecuciones solapadas se pisarían
la base sin dejar rastro en ningún log. La subida va con `IfMatch` y el ETag que
leí, de modo que la segunda recibe un 412 y falla en rojo en vez de borrar el
trabajo de la primera. Esto deja de valer el día que haya escritores concurrentes
de verdad, y entonces la señal para migrar es clara: el primer 412 legítimo.
