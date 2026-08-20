"""Stack desechable: una Lambda que responde si el WAF de gob.pe deja cosechar.

Es un stack APARTE del de la Fase 1 a propósito. Se despliega, se invoca una vez
y se destruye; mezclarlo con la infraestructura que se queda obligaría a acordarse
de limpiarlo después, y eso es exactamente lo que nadie hace.

Sin Function URL: se invoca con `aws lambda invoke`, que autentica con mis
credenciales. Publicar una URL para esto sería exponer un endpoint a internet
para una pregunta que me hago yo solo.
"""
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from constructs import Construct


class SondaWafStack(Stack):
    def __init__(self, ambito: Construct, id_: str, *,
                 activo: lambda_.Code, **kw) -> None:
        super().__init__(ambito, id_, **kw)

        logs_ = logs.LogGroup(
            self, "SondaLogs",
            retention=logs.RetentionDays.ONE_DAY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        fn = lambda_.Function(
            self, "SondaFn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="sonda.handler",
            code=activo,
            # La cadena son 4 páginas de colección + página de mes + PDF, cada
            # petición con timeout de 40-60 s y hasta 3 reintentos. 120 s deja
            # margen para que un bloqueo se manifieste como bloqueo y no como
            # timeout de la Lambda, que sería un diagnóstico distinto.
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                # harvester.download() escribe el PDF; en Lambda solo /tmp es
                # escribible.
                "PRECIOVIVO_RAW": "/tmp/raw",
            },
            log_group=logs_,
        )

        # SIN permisos adicionales. La sonda solo habla con internet, y salir a
        # internet no lo concede IAM: lo concede estar fuera de una VPC. El rol
        # se queda con AWSLambdaBasicExecutionRole (escribir en CloudWatch Logs)
        # y nada más.

        CfnOutput(self, "NombreFuncion", value=fn.function_name)
        CfnOutput(self, "GrupoDeLogs", value=logs_.log_group_name)
