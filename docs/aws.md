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
cd infra && cdk destroy PrecioVivoFase1
```

Borra los 13 recursos, **incluido el bucket con su contenido** (de eso se ocupa
`auto_delete_objects`).

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
