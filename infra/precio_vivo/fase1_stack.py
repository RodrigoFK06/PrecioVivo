"""Fase 1: el snapshot servido desde Lambda.

QUÉ HAY AQUÍ Y POR QUÉ CADA PIEZA
---------------------------------
S3           guarda el snapshot. Versionado, porque el objeto se reescribe cada
             día hábil y una publicación defectuosa se revierte volviendo a la
             versión anterior — no hay que regenerar nada.
Lambda       lee ese objeto UNA vez por contenedor y responde desde memoria.
             Ver el docstring de `lambda/handler.py`, que es donde vive el
             argumento.
Function URL en vez de API Gateway. API Gateway aporta autorizadores, throttling
             por clave, dominios propios y WAF; nada de eso se usa hoy, y cobra
             ~1 USD por millón de peticiones que la Function URL no cobra.
             Cuando haga falta un dominio propio o WAF, se migra.

LO QUE NO HAY, Y ES DELIBERADO
------------------------------
Sin VPC. La función solo habla con S3, y S3 se alcanza por el endpoint público
de AWS. Meter la Lambda en una VPC obligaría a un NAT Gateway o a un endpoint
de VPC para que pudiera salir: ~32 USD/mes de algo que no aporta nada aquí. Una
Lambda fuera de VPC no es "menos segura": no expone puertos, y su superficie es
el rol de IAM, que abajo está acotado a un único objeto de un único bucket.
"""
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as ddb,
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
from constructs import Construct

CLAVE_SNAPSHOT = "snapshot.json"


class Fase1Stack(Stack):
    def __init__(self, ambito: Construct, id_: str, **kw) -> None:
        super().__init__(ambito, id_, **kw)

        # --- S3 -----------------------------------------------------------
        bucket = s3.Bucket(
            self, "SnapshotBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # Es una cuenta de laboratorio: `cdk destroy` debe dejarla limpia.
            # En producción esto sería RETAIN — borrar el bucket de datos por un
            # `destroy` accidental es justo el fallo que no se puede permitir.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                # El versionado sin caducidad crece para siempre: 3,4 MB por día
                # hábil son ~900 MB al año en versiones que nadie va a leer.
                s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(30),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                ),
            ],
        )


        # --- DynamoDB: telemetria de consultas (Fase 2) --------------------
        # PK = endpoint#fecha   SK = timestamp#id
        # El porque completo esta en el docstring de `_telemetria` en el
        # handler; el resumen: la PK de DynamoDB solo admite igualdad, asi que
        # `PK = timestamp` haria imposible consultar por rango sin un Scan. La
        # alternativa `PK = fecha` responderia mas preguntas con menos consultas,
        # pero concentra todas las escrituras del dia en una particion.
        tabla = ddb.Table(
            self, "TelemetriaTable",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            # Bajo demanda: no se paga por estar encendida. Con trafico
            # irregular (una API que casi nadie usa y de pronto una demo) la
            # capacidad aprovisionada seria pagar por un pico que rara vez llega.
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            # Los borrados por TTL no se cobran. Sin esto la tabla crece para
            # siempre con datos que nadie va a consultar.
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # La Fase 3 escribe aquí el snapshot y guarda su estado bajo `estado/`.
        # Se expone el bucket en vez de crear uno nuevo: el export TIENE que
        # escribir en este objeto de todos modos, así que el acoplamiento entre
        # fases ya existe; duplicar el bucket solo lo escondería.
        self.bucket = bucket

        # --- Lambda -------------------------------------------------------
        # Grupo de logs explícito en vez de `log_retention=`. Ese atajo despliega
        # una Lambda auxiliar cuyo único trabajo es llamar a PutRetentionPolicy,
        # con su propio rol y sus propios permisos: más superficie de IAM que la
        # función que de verdad hace el trabajo. Declararlo aquí es un recurso
        # menos y un permiso menos.
        grupo_logs = logs.LogGroup(
            self, "ConsultaFnLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        fn = lambda_.Function(
            self, "ConsultaFn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            # Directorio ensamblado por `app.py`: handler + los 3 módulos de
            # `preciovivo` que los endpoints de lectura necesitan. Sin
            # dependencias de terceros, así que sin capas y sin contenedor: el
            # zip pesa ~50 KB contra el límite de 250 MB descomprimido.
            code=lambda_.Code.from_asset("build/lambda"),
            memory_size=512,
            # 512 MB no es por RAM (el snapshot ocupa ~12 MB): en Lambda la CPU
            # escala con la memoria, y el arranque en frío está dominado por el
            # parseo de JSON, que es CPU pura. Con 128 MB el frío se dispara.
            timeout=Duration.seconds(10),
            environment={
                "SNAPSHOT_BUCKET": bucket.bucket_name,
                "SNAPSHOT_KEY": CLAVE_SNAPSHOT,
                "TABLA_TELEMETRIA": tabla.table_name,
                # Interruptor para medir cuanto cuesta la propia telemetria.
                "TELEMETRIA": "1",
                "DIAS_TTL": "30",
            },
            # Los logs sin caducidad se guardan para siempre y se pagan para
            # siempre. Una semana sobra para depurar un laboratorio.
            log_group=grupo_logs,
        )

        # --- IAM: un permiso, un recurso, una razón ------------------------
        fn.add_to_role_policy(iam.PolicyStatement(
            # `GetObject` porque el handler descarga el snapshot al arrancar.
            # Nada más: la función no escribe, no lista y no borra.
            actions=["s3:GetObject"],
            # Un ÚNICO objeto, no el bucket entero. Si mañana se guardan ahí
            # los artefactos del RAG, esta función seguirá sin poder leerlos.
            resources=[bucket.arn_for_objects(CLAVE_SNAPSHOT)],
        ))
        # Nota: NO se usa `bucket.grant_read(fn)`. Ese atajo concede también
        # `s3:GetObject*`, `s3:GetBucket*` y `s3:List*` sobre el bucket entero.
        # Funciona, pero concede de más y no sabrías explicar por qué.

        fn.add_to_role_policy(iam.PolicyStatement(
            # Solo PutItem: la funcion ESCRIBE telemetria y nunca la lee. Las
            # consultas de analisis las hace un operador con sus credenciales,
            # no la Lambda. Sin Query, sin Scan, sin DeleteItem.
            actions=["dynamodb:PutItem"],
            resources=[tabla.table_arn],
        ))
        # De nuevo se evita `tabla.grant_write_data(fn)`: ese atajo concede
        # tambien BatchWriteItem, UpdateItem y DeleteItem.

        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        # NONE = pública. Es un endpoint de solo lectura sobre datos que ya se
        # publican en precio-vivo.vercel.app, así que no hay nada que proteger.
        # Si algún día sirviera datos no públicos, esto pasa a AWS_IAM y el
        # llamante firma con SigV4.

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "BucketArn", value=bucket.bucket_arn)
        CfnOutput(self, "FunctionArn", value=fn.function_arn)
        CfnOutput(self, "FunctionRoleArn", value=fn.role.role_arn)
        CfnOutput(self, "FunctionUrl", value=url.url)
        CfnOutput(self, "TablaTelemetria", value=tabla.table_name)
        CfnOutput(self, "TablaTelemetriaArn", value=tabla.table_arn)
        CfnOutput(self, "SubirSnapshot", value=(
            f"aws s3 cp web/data/snapshot.json s3://{bucket.bucket_name}/{CLAVE_SNAPSHOT}"))
