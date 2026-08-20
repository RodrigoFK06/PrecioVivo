"""Fase 3: la ingesta diaria, orquestada.

QUÉ HAY AQUÍ
------------
EventBridge Scheduler  dispara a las 08:00 de Lima. Zona horaria nativa: no se
                       hardcodea 13:00 UTC ni se escribe un comentario
                       explicando la resta.
Step Functions         encadena los pasos. Standard y no Express, porque Express
                       corta a los 5 minutos.
7 Lambdas              cosecha, contraste SISAP, boletín multi-mercado,
                       listado, un trabajador por producto, reducción, export y
                       publicación. Están separadas porque sus
                       dependencias, su duración y su política de reintentos son
                       distintas.
S3                     el bucket de la Fase 1, con prefijo `estado/`. No hace
                       falta uno nuevo: el export tiene que escribir ahí de todos
                       modos, así que el acoplamiento ya existía.

POR QUÉ ESTÁ REPARTIDO EL FORECAST
-----------------------------------
Medido: ~31 minutos en serie contra un límite duro de 15 por Lambda. El trabajo
de cada producto son ~25 s y es independiente del de los demás, así que un `Map`
lo baja a ~1 minuto de reloj. Ver el docstring de `lambda_forecast/`.

LA POLÍTICA DE REINTENTOS, que es lo que distingue una tubería de un cron
-------------------------------------------------------------------------
No es la misma para todos los pasos, y ésa es la idea:

  cosecha y export -> States.ALL, 3 intentos, backoff exponencial.
      Fallan por red o por S3, que son transitorios. Y se pueden reintentar sin
      pensarlo porque son IDEMPOTENTES: `upsert_precios` re-escribe lo mismo y
      el snapshot se regenera entero. Si no lo fueran, el reintento sería el bug.

  trabajadores del forecast -> SOLO errores de infraestructura de Lambda.
      Un cálculo determinista que falló va a volver a fallar; reintentarlo es
      gastar dinero para llegar al mismo sitio más tarde. Lo único que se
      reintenta es que Lambda misma tenga un mal día.

  el parseo -> NUNCA.
      Y no por política, sino por estructura: un PDF que no se parsea no lanza
      excepción, sale como `bloqueados` en el resultado y la máquina deriva a un
      estado Fail. No hay reintento posible porque no hay error que reintentar.

EL FALLO VISIBLE
----------------
`ingest.main()` termina con `return 0` pase lo que pase. La compuerta MIN_ROWS
bloquea la carga de un reporte roto — bien — pero después el proceso sale con
éxito, así que un cambio de layout del PDF deja la tarea diaria en verde
mientras deja de entrar el dato.

Aquí eso es un estado `Fail`. La ejecución queda en rojo en la consola y se puede
alarmar. Una compuerta que no avisa no es una compuerta.
"""
import json

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as tareas,
)
from constructs import Construct

PREFIJO_ESTADO = "estado/"
CLAVE_DB = PREFIJO_ESTADO + "preciovivo.db"
CLAVE_CACHE = PREFIJO_ESTADO + "forecast_cache.json"
CLAVE_SISAP = PREFIJO_ESTADO + "sisap_check.json"
CLAVE_SNAPSHOT = "snapshot.json"
PREFIJO_RAG = "rag/"
# El nombre del parámetro, NO su valor. El secreto se crea una vez a mano y se
# lee en tiempo de ejecución: un secreto en la definición de la infraestructura
# acaba en el repositorio, en el historial de CloudFormation y en los logs de
# despliegue.
PARAM_CLAVE_EMBED = "/preciovivo/embed-api-key"
PARAM_TOKEN_GITHUB = "/preciovivo/github-token"
REPO_GITHUB = "RodrigoFK06/PrecioVivo"

# Lambda asigna una vCPU completa alrededor de los 1.769 MB. Por debajo, el GBM
# —que es CPU pura y de un solo hilo— va proporcionalmente más lento. Por encima
# no gana nada, porque no paraleliza. Así que ése es el punto, no un número
# redondo elegido a ojo.
MB_UNA_VCPU = 1769


class Fase3Stack(Stack):
    def __init__(self, ambito: Construct, id_: str, *,
                 bucket: s3.IBucket, activos: dict, **kw) -> None:
        super().__init__(ambito, id_, **kw)

        entorno_estado = {
            "BUCKET_ESTADO": bucket.bucket_name,
            "CLAVE_DB": CLAVE_DB,
            "CLAVE_CACHE_FORECAST": CLAVE_CACHE,
            "CLAVE_SISAP": CLAVE_SISAP,
        }

        def nueva_fn(nombre, paquete, handler, memoria, segundos, entorno=None):
            """Una Lambda con su grupo de logs explícito.

            El grupo va declarado y no por `log_retention`: esa propiedad está
            obsoleta y despliega una Lambda extra de custom resource solo para
            fijar la retención. Un recurso más que mantener por una línea menos.
            """
            grupo = logs.LogGroup(
                self, f"{nombre}Logs",
                retention=logs.RetentionDays.TWO_WEEKS,
                removal_policy=RemovalPolicy.DESTROY,
            )
            return lambda_.Function(
                self, nombre,
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler=handler,
                code=activos[paquete],
                memory_size=memoria,
                timeout=Duration.seconds(segundos),
                environment={**entorno_estado, **(entorno or {})},
                log_group=grupo,
            )

        # --- Las cinco funciones -------------------------------------------
        # Cosecha: red (gob.pe está a ~3 s por petición desde AWS, medido) más el
        # parseo de hasta 5 PDFs con pdfplumber. 600 s es holgura, no coste: se
        # factura el tiempo real, no el timeout.
        fn_ingesta = nueva_fn("Ingesta", "ingesta", "ingesta.handler",
                              1024, 600, {"DIAS_A_COSECHAR": "5"})

        # Mismo paquete que la cosecha (requests + pdfplumber): SISAP también
        # baja un PDF y lo parsea. Otra función, no otro paquete.
        fn_sisap = nueva_fn("Sisap", "ingesta", "sisap_lambda.handler", 1024, 300)

        # Mismo paquete que la cosecha: el boletín es otro PDF de gob.pe.
        # 600 s porque navega la colección 338 (5 peticiones a ~3 s desde AWS)
        # antes de descargar y parsear.
        fn_boletin = nueva_fn("Boletin", "ingesta", "boletin_lambda.handler",
                              1024, 600)

        fn_listar = nueva_fn("ForecastListar", "forecast",
                             "forecast_lambda.listar", 1024, 120)

        fn_producto = nueva_fn("ForecastProducto", "forecast",
                               "forecast_lambda.producto", MB_UNA_VCPU, 300)

        fn_reducir = nueva_fn("ForecastReducir", "forecast",
                              "forecast_lambda.reducir", 1024, 300)

        fn_indice = nueva_fn(
            "Indice", "indice", "indexar.handler", 2048, 600,
            {"BUCKET_SNAPSHOT": bucket.bucket_name,
             "CLAVE_SNAPSHOT": CLAVE_SNAPSHOT,
             "PREFIJO_RAG": PREFIJO_RAG,
             "PREFIJO_ESTADO": PREFIJO_ESTADO,
             "PARAM_CLAVE_EMBED": PARAM_CLAVE_EMBED,
             # Estos tres definen la FIRMA del embebedor, y la firma es lo que
             # ata el índice publicado a la consulta que hace el sitio. Van aquí
             # y no en el código porque `embeddings.py` los lee en tiempo de
             # import: fijarlos dentro del handler llega tarde.
             "EMBED_BASE_URL": "https://api.jina.ai/v1",
             "EMBED_MODEL": "jina-embeddings-v3",
             "EMBED_DIMS": "256"})

        fn_publicar = nueva_fn(
            "Publicar", "publicar", "publicar.handler", 512, 300,
            {"BUCKET_SNAPSHOT": bucket.bucket_name,
             "CLAVE_SNAPSHOT": CLAVE_SNAPSHOT,
             "PREFIJO_RAG": PREFIJO_RAG,
             "PARAM_TOKEN_GITHUB": PARAM_TOKEN_GITHUB,
             "REPO_GITHUB": REPO_GITHUB})

        fn_export = nueva_fn("Export", "export", "exportar.handler",
                             1024, 300,
                             {"BUCKET_SNAPSHOT": bucket.bucket_name,
                              "CLAVE_SNAPSHOT": CLAVE_SNAPSHOT})

        # --- IAM: una sentencia por necesidad real --------------------------
        #
        # POR QUÉ HAY UN ListBucket AQUÍ, que parece contradecir todo lo anterior
        # ----------------------------------------------------------------------
        # Sin `s3:ListBucket`, S3 responde AccessDenied —no NoSuchKey— cuando el
        # objeto NO EXISTE. Lo hace a propósito: sin permiso de listar, decir
        # "no existe" ya revelaría información sobre el contenido del bucket.
        #
        # La consecuencia es que el código que distingue "todavía no está" de
        # "no puedo leerlo" deja de funcionar. Pasó de verdad: `bajar_sisap()`
        # capturaba NoSuchKey, recibió AccessDenied y tumbó la ejecución entera
        # el primer día en que el contraste no existía.
        #
        # Se concede acotado por prefijo: solo permite listar bajo `estado/`, que
        # es donde vive el estado del pipeline. No da acceso al bucket entero ni
        # al snapshot publicado.
        listar_estado = iam.PolicyStatement(
            actions=["s3:ListBucket"],
            resources=[bucket.bucket_arn],
            conditions={"StringLike": {"s3:prefix": [PREFIJO_ESTADO + "*"]}},
        )

        # Ninguna función recibe el bucket entero. `grant_read`/`grant_write` de
        # CDK conceden familias completas (GetObject*, GetBucket*, List*), y aquí
        # se sabe exactamente qué objeto toca cada paso.

        # La cosecha lee la BD y la vuelve a escribir.
        fn_ingesta.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_DB)],
        ))

        # Listado y trabajadores solo LEEN. No pueden escribir la BD ni por error:
        # 73 escritores concurrentes sobre el mismo objeto es exactamente el fallo
        # que no debe ser posible.
        for fn in (fn_listar, fn_producto):
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[bucket.arn_for_objects(CLAVE_DB)],
            ))

        # Todas las que consultan un objeto que PUEDE no existir todavía.
        for fn in (fn_ingesta, fn_sisap, fn_export):
            fn.add_to_role_policy(listar_estado)

        # SISAP ingesta AVES (escribe la BD) y publica el contraste.
        fn_sisap.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_DB),
                       bucket.arn_for_objects(CLAVE_SISAP)],
        ))

        # El boletín ingesta las frutas: lee la BD y la reescribe.
        fn_boletin.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_DB)],
        ))
        fn_boletin.add_to_role_policy(listar_estado)

        # La reducción escribe el caché del forecast y el historial de
        # pronósticos (que va dentro de la BD).
        fn_reducir.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_DB),
                       bucket.arn_for_objects(CLAVE_CACHE)],
        ))

        # El export lee el estado y escribe el snapshot que sirve la Fase 1.
        fn_export.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[bucket.arn_for_objects(CLAVE_DB),
                       bucket.arn_for_objects(CLAVE_CACHE),
                       bucket.arn_for_objects(CLAVE_SISAP)],
        ))
        fn_export.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_SNAPSHOT)],
        ))

        # El indexado lee el snapshot publicado, escribe el artefacto RAG y —lo
        # que de verdad importa— lee y reescribe el CACHÉ de embeddings. Sin ese
        # caché, cada corrida diaria re-embebería el corpus entero: ~1,5 M tokens
        # de una cuota de 5 M. Ver el docstring de lambda_indice/.
        fn_indice.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[bucket.arn_for_objects(CLAVE_SNAPSHOT)],
        ))
        fn_indice.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects(PREFIJO_RAG + "*"),
                       bucket.arn_for_objects(PREFIJO_ESTADO + "embed_cache.*")],
        ))
        fn_indice.add_to_role_policy(listar_estado)
        # Solo GetParameter, y solo ESE parámetro. Sin `ssm:GetParametersByPath`,
        # que dejaría leer todo el árbol /preciovivo/.
        fn_indice.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARAM_CLAVE_EMBED}"],
        ))

        # El publicador lee lo que hay que subir al repositorio y su token.
        fn_publicar.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[bucket.arn_for_objects(CLAVE_SNAPSHOT),
                       bucket.arn_for_objects(PREFIJO_RAG + "*")],
        ))
        fn_publicar.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARAM_TOKEN_GITHUB}"],
        ))

        # --- La máquina de estados ------------------------------------------
        paso_ingesta = tareas.LambdaInvoke(
            self, "Cosechar",
            lambda_function=fn_ingesta,
            payload_response_only=True,
            result_path="$.ingesta",
        )
        # Red y S3: transitorios, y el paso es idempotente.
        paso_ingesta.add_retry(errors=["States.ALL"], max_attempts=3,
                               interval=Duration.seconds(10), backoff_rate=2.0)

        compuerta_rota = sfn.Fail(
            self, "CompuertaDeEscritura",
            error="CompuertaDeEscritura",
            cause=("Se encontraron reportes y TODOS quedaron bloqueados por "
                   "MIN_ROWS. Los PDFs están publicados y ya no sabemos leerlos: "
                   "es la firma de un cambio de layout. Ver ingesta.mensajes en "
                   "la entrada de este estado."),
        )

        paso_sisap = tareas.LambdaInvoke(
            self, "ContrastarSisap",
            lambda_function=fn_sisap,
            payload_response_only=True,
            result_path="$.sisap",
        )
        paso_sisap.add_retry(errors=["States.ALL"], max_attempts=2,
                             interval=Duration.seconds(10), backoff_rate=2.0)

        paso_boletin = tareas.LambdaInvoke(
            self, "CosecharBoletin",
            lambda_function=fn_boletin,
            payload_response_only=True,
            result_path="$.boletin",
        )
        paso_boletin.add_retry(errors=["States.ALL"], max_attempts=2,
                               interval=Duration.seconds(10), backoff_rate=2.0)

        paso_listar = tareas.LambdaInvoke(
            self, "ListarProductos",
            lambda_function=fn_listar,
            payload_response_only=True,
            result_path="$.listado",
        )
        paso_listar.add_retry(errors=["States.ALL"], max_attempts=3,
                              interval=Duration.seconds(5), backoff_rate=2.0)

        paso_producto = tareas.LambdaInvoke(
            self, "PronosticarProducto",
            lambda_function=fn_producto,
            payload_response_only=True,
        )
        # SIN States.ALL a propósito. CDK ya añade por defecto los errores de
        # infraestructura de Lambda (ServiceException, TooManyRequests,
        # SdkClientException, AWSLambdaException) y ésos son los únicos que tiene
        # sentido reintentar: el cálculo es determinista.

        mapa = sfn.Map(
            self, "PorProducto",
            items_path=sfn.JsonPath.string_at("$.listado.nombres"),
            item_selector={"nombre": sfn.JsonPath.string_at("$$.Map.Item.Value")},
            # 8, y el número NO lo elige el diseño: lo elige la cuota.
            #
            #     aws lambda get-account-settings
            #     ConcurrentExecutions: 10
            #
            # Una cuenta nueva de AWS arranca con 10 ejecuciones concurrentes, no
            # con las 1.000 que suele suponerse. Con max_concurrency=20 esto
            # fallaba con Lambda.TooManyRequestsException (429) y agotaba los
            # reintentos. Se dejan 2 libres para la Lambda de consultas de la
            # Fase 1, que comparte el mismo cupo.
            #
            # Coste: 73 productos en tandas de 8 son ~9 tandas de ~25 s, o sea
            # unos 4 minutos de reloj en vez de minuto y medio. Sigue siendo
            # ocho veces mejor que los 31 minutos en serie.
            #
            # Se sube pidiendo cuota (Service Quotas, L-B99A9384). No se pide
            # aquí: cambiar los límites de una cuenta no es algo que deba hacer
            # el código de infraestructura por su cuenta.
            max_concurrency=8,
            result_path="$.partes",
        )
        mapa.item_processor(paso_producto)

        paso_reducir = tareas.LambdaInvoke(
            self, "Reducir",
            lambda_function=fn_reducir,
            payload_response_only=True,
            payload=sfn.TaskInput.from_object({
                "partes": sfn.JsonPath.object_at("$.partes"),
            }),
            result_path="$.forecast",
        )
        paso_reducir.add_retry(errors=["States.ALL"], max_attempts=2,
                               interval=Duration.seconds(5), backoff_rate=2.0)

        paso_export = tareas.LambdaInvoke(
            self, "Exportar",
            lambda_function=fn_export,
            payload_response_only=True,
            result_path="$.export",
        )
        paso_export.add_retry(errors=["States.ALL"], max_attempts=3,
                              interval=Duration.seconds(5), backoff_rate=2.0)

        paso_indice = tareas.LambdaInvoke(
            self, "IndexarRag",
            lambda_function=fn_indice,
            payload_response_only=True,
            result_path="$.indice",
        )
        paso_indice.add_retry(errors=["States.ALL"], max_attempts=2,
                              interval=Duration.seconds(10), backoff_rate=2.0)

        paso_publicar = tareas.LambdaInvoke(
            self, "PublicarEnGitHub",
            lambda_function=fn_publicar,
            payload_response_only=True,
            result_path="$.publicacion",
        )
        paso_publicar.add_retry(errors=["States.ALL"], max_attempts=3,
                                interval=Duration.seconds(10), backoff_rate=2.0)

        decision = sfn.Choice(self, "PasoLaCompuerta")
        decision.when(
            sfn.Condition.boolean_equals("$.ingesta.fallo_de_compuerta", True),
            compuerta_rota,
        )
        # SISAP NO puede tumbar la publicación: los precios del 335 ya están
        # cargados y el sitio tiene que salir igual, solo que sin el bloque de
        # verificación. El Catch salta al siguiente paso y deja el error en
        # `$.sisapError`, donde queda visible en el historial de la ejecución
        # —a diferencia del Write-Warning del script de Windows, que se lo
        # llevaba una consola que nadie mira.
        paso_sisap.add_catch(paso_boletin, errors=["States.ALL"],
                             result_path="$.sisapError")

        # El boletín tampoco bloquea: si la colección 338 no publica hoy o cambia
        # el layout de su grid, el sitio sale con los 73 productos del 335 en vez
        # de 147 — menos catálogo, pero al día.
        paso_boletin.add_catch(paso_listar, errors=["States.ALL"],
                               result_path="$.boletinError")

        publicado = sfn.Succeed(self, "Publicado")

        # El índice RAG tampoco puede tumbar la publicación. Si falla —sin clave,
        # cuota agotada, tope de embeddings superado— el sitio degrada a
        # catálogo-en-contexto y sigue respondiendo, solo que sin recuperación
        # vectorial de los días nuevos. Ese es el mismo criterio que ya tenía el
        # script de Windows, donde el indexado iba dentro de un try/catch.
        # El indexado puede fallar y aun así hay que publicar el snapshot: el
        # sitio degrada a catálogo-en-contexto pero con los precios del día.
        paso_indice.add_catch(paso_publicar, errors=["States.ALL"],
                              result_path="$.indiceError")

        decision.otherwise(
            paso_sisap.next(paso_boletin).next(paso_listar).next(mapa).next(paso_reducir)
            .next(paso_export).next(paso_indice).next(paso_publicar)
            .next(publicado)
        )

        maquina = sfn.StateMachine(
            self, "Diaria",
            definition_body=sfn.DefinitionBody.from_chainable(
                paso_ingesta.next(decision)),
            # Standard y no Express: Express corta a los 5 minutos y esto dura
            # más. Standard además guarda el historial completo de cada ejecución
            # durante 90 días sin configurar nada, que es la razón de que aquí NO
            # se active el registro en CloudWatch Logs: duplicaría el mismo dato
            # y sí cobraría por él.
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(1),
        )

        # --- El disparo diario ----------------------------------------------
        rol_programador = iam.Role(
            self, "RolProgramador",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        rol_programador.add_to_policy(iam.PolicyStatement(
            actions=["states:StartExecution"],
            resources=[maquina.state_machine_arn],
        ))

        scheduler.CfnSchedule(
            self, "Diario",
            # OFF = a la hora exacta. La ventana flexible existe para repartir
            # carga entre muchos disparos; con uno al día solo añadiría
            # incertidumbre sobre cuándo corrió.
            flexible_time_window={"mode": "OFF"},
            # 08:00 de Lima. EventBridge Scheduler entiende la zona, así que no
            # hay una resta a UTC escrita a mano que alguien tenga que revisar.
            schedule_expression="cron(0 8 ? * MON-FRI *)",
            schedule_expression_timezone="America/Lima",
            description="Precio Vivo: cosecha, pronostica y publica el snapshot",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=maquina.state_machine_arn,
                role_arn=rol_programador.role_arn,
                input=json.dumps({"dias": 5}),
            ),
        )
        # LUNES A VIERNES, y el motivo es el costo, no el calendario.
        #
        # El Map genera ~2 transiciones por producto: con 73 productos son ~154
        # por corrida. La capa gratuita de Step Functions son 4.000 transiciones
        # al mes, perpetuas.
        #
        #     30 corridas x 154 = 4.620  ->  620 de más  ->  ~0,015 USD/mes
        #     22 corridas x 154 = 3.388  ->  dentro de la capa gratuita
        #
        # Antes esto corría todos los días, y el argumento era bueno: un día sin
        # reporte es un no-op en verde, y correr también en fin de semana daba la
        # propiedad de que el sábado recogiera un 335 publicado tarde el viernes.
        #
        # Se cambia porque el requisito es gasto CERO, no gasto bajo. Y lo que se
        # pierde es acotado: `--latest 5` sigue curando la tubería sola, así que
        # un reporte publicado el viernes por la noche entra el lunes. El costo
        # real es un fin de semana de datos viejos en el sitio, no un dato perdido.
        #
        # El día que el gasto deje de ser cero, volver a diario es cambiar cinco
        # caracteres.

        # Lo consume `PrecioVivoAlarmas`, que vive en su propio stack para
        # sobrevivir a que esta fase se rehaga.
        self.arn_maquina = maquina.state_machine_arn

        CfnOutput(self, "MaquinaDeEstados", value=maquina.state_machine_arn)
        CfnOutput(self, "FnIngesta", value=fn_ingesta.function_name)
        CfnOutput(self, "FnSisap", value=fn_sisap.function_name)
        CfnOutput(self, "FnBoletin", value=fn_boletin.function_name)
        CfnOutput(self, "FnIndice", value=fn_indice.function_name)
        CfnOutput(self, "FnPublicar", value=fn_publicar.function_name)
        CfnOutput(self, "FnListar", value=fn_listar.function_name)
        CfnOutput(self, "FnProducto", value=fn_producto.function_name)
        CfnOutput(self, "FnReducir", value=fn_reducir.function_name)
        CfnOutput(self, "FnExport", value=fn_export.function_name)
