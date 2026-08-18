"""Fase 3: la ingesta diaria, orquestada.

QUÉ HAY AQUÍ
------------
EventBridge Scheduler  dispara a las 08:00 de Lima. Zona horaria nativa: no se
                       hardcodea 13:00 UTC ni se escribe un comentario
                       explicando la resta.
Step Functions         encadena los pasos. Standard y no Express, porque Express
                       corta a los 5 minutos.
5 Lambdas              cosecha, listado, un trabajador por producto, reducción y
                       export. Están separadas porque sus dependencias, su
                       duración y su política de reintentos son distintas.
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
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tareas,
)
from constructs import Construct

PREFIJO_ESTADO = "estado/"
CLAVE_DB = PREFIJO_ESTADO + "preciovivo.db"
CLAVE_CACHE = PREFIJO_ESTADO + "forecast_cache.json"
CLAVE_SNAPSHOT = "snapshot.json"

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

        fn_listar = nueva_fn("ForecastListar", "forecast",
                             "forecast_lambda.listar", 1024, 120)

        fn_producto = nueva_fn("ForecastProducto", "forecast",
                               "forecast_lambda.producto", MB_UNA_VCPU, 300)

        fn_reducir = nueva_fn("ForecastReducir", "forecast",
                              "forecast_lambda.reducir", 1024, 300)

        fn_export = nueva_fn("Export", "export", "exportar.handler",
                             1024, 300,
                             {"BUCKET_SNAPSHOT": bucket.bucket_name,
                              "CLAVE_SNAPSHOT": CLAVE_SNAPSHOT})

        # --- IAM: una sentencia por necesidad real --------------------------
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
                       bucket.arn_for_objects(CLAVE_CACHE)],
        ))
        fn_export.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[bucket.arn_for_objects(CLAVE_SNAPSHOT)],
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

        decision = sfn.Choice(self, "PasoLaCompuerta")
        decision.when(
            sfn.Condition.boolean_equals("$.ingesta.fallo_de_compuerta", True),
            compuerta_rota,
        )
        decision.otherwise(
            paso_listar.next(mapa).next(paso_reducir).next(paso_export)
            .next(sfn.Succeed(self, "Publicado"))
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
            schedule_expression="cron(0 8 * * ? *)",
            schedule_expression_timezone="America/Lima",
            description="Precio Vivo: cosecha, pronostica y publica el snapshot",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=maquina.state_machine_arn,
                role_arn=rol_programador.role_arn,
                input=json.dumps({"dias": 5}),
            ),
        )
        # Todos los días, también sábado y domingo. Un día sin reporte nuevo es un
        # no-op que termina en verde, y `--latest 5` hace que la tubería se cure
        # sola: si un día falla, el siguiente recupera lo que faltaba. Programarla
        # solo de lunes a viernes ahorraría dos ejecuciones vacías al mes y
        # perdería esa propiedad.

        CfnOutput(self, "MaquinaDeEstados", value=maquina.state_machine_arn)
        CfnOutput(self, "FnIngesta", value=fn_ingesta.function_name)
        CfnOutput(self, "FnListar", value=fn_listar.function_name)
        CfnOutput(self, "FnProducto", value=fn_producto.function_name)
        CfnOutput(self, "FnReducir", value=fn_reducir.function_name)
        CfnOutput(self, "FnExport", value=fn_export.function_name)
