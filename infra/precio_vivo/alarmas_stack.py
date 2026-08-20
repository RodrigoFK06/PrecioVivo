"""Alarmas: convertir "falla de forma visible" en "me entero".

EL HUECO QUE ESTE STACK CIERRA
-------------------------------
La Fase 3 se diseñó para fallar de forma visible: la compuerta de escritura
deriva a un estado `Fail`, los pasos no bloqueantes dejan su error en el
historial de la ejecución, y cada Lambda devuelve el porqué en su resultado.

Todo eso es visible **en la consola de Step Functions**. Nadie mira la consola de
Step Functions a las ocho de la mañana. Visible no es notificado, y hasta este
stack el sistema entero no tenía una sola alarma:

    aws cloudwatch describe-alarms  ->  0
    aws sns list-topics             ->  0

QUÉ SE ALARMA Y QUÉ NO
----------------------
Se alarma lo que significa "la tubería intentó y no pudo":

    ExecutionsFailed     un paso agotó sus reintentos, o saltó la compuerta
    ExecutionsTimedOut   la ejecución superó la hora de tope

NO se alarma "la tubería no corrió", y no por olvido. Alarmar sobre ausencia
exige una ventana, y aquí la ventana honesta es enorme: la máquina corre de lunes
a viernes, así que entre la corrida del viernes y la del lunes pasan 72 horas
legítimas. Una alarma de ausencia con ventana corta sonaría todos los sábados, y
una alarma que suena cuando no pasa nada deja de mirarse — que es exactamente el
fallo que este archivo intenta evitar.

Esa vigilancia ya existe y en el sitio correcto: el trabajo
`Dead-man's-switch (frescura del dato)` de `.github/workflows/ci.yml` comprueba a
diario si el snapshot publicado envejeció. Mide el SÍNTOMA (el dato está viejo)
en vez de la CAUSA (la máquina no arrancó), que es más robusto: cubre también los
casos en que la máquina corrió y publicó basura.

POR QUÉ UN STACK APARTE
-----------------------
Las alarmas sobreviven a las fases. Si mañana se rehace la Fase 3, se destruye
`PrecioVivoFase3` y las alarmas se van con ella justo cuando más falta hacen.
Separadas, se apuntan a lo que haya.

COSTO
-----
CloudWatch regala 10 alarmas de resolución estándar; aquí van 2. SNS regala
1.000 notificaciones por correo al mes; aquí caben unas 22 como mucho, y solo si
todo va mal. Total: 0,00 USD.
"""
from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as acciones,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct


class AlarmasStack(Stack):
    def __init__(self, ambito: Construct, id_: str, *,
                 arn_maquina: str, correo: str | None = None, **kw) -> None:
        super().__init__(ambito, id_, **kw)

        tema = sns.Topic(self, "Alertas",
                         display_name="Precio Vivo · alertas")

        # La suscripción es OPCIONAL y va por contexto de CDK, no escrita aquí:
        #     cdk deploy PrecioVivoAlarmas -c correo_alertas=tu@correo
        #
        # Un correo dentro del código acaba en el repositorio público. Y AWS
        # exige confirmar la suscripción desde el propio buzón, así que este
        # paso no se puede automatizar del todo aunque se quisiera.
        if correo:
            tema.add_subscription(subs.EmailSubscription(correo))

        # El nombre de la máquina, extraído del ARN, para que el mensaje de la
        # alarma diga a qué se refiere sin abrir la consola.
        nombre_maquina = arn_maquina.rsplit(":", 1)[-1]

        def alarma(id_alarma: str, metrica: str, descripcion: str) -> cw.Alarm:
            a = cw.Alarm(
                self, id_alarma,
                metric=cw.Metric(
                    namespace="AWS/States",
                    metric_name=metrica,
                    dimensions_map={"StateMachineArn": arn_maquina},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=0,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluation_periods=1,
                # NOT_BREACHING y no MISSING: la mayor parte del día no hay
                # ejecuciones y por tanto no hay dato. Sin esto, la alarma
                # viviría en estado INSUFFICIENT_DATA y el correo llegaría por
                # cambios de estado que no significan nada.
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=descripcion,
            )
            a.add_alarm_action(acciones.SnsAction(tema))
            return a

        alarma(
            "EjecucionFallida", "ExecutionsFailed",
            f"La tubería diaria de Precio Vivo ({nombre_maquina}) terminó en "
            f"FAILED. Causas típicas: la compuerta de escritura bloqueó todos "
            f"los reportes (cambio de layout del PDF), o un paso agotó sus "
            f"reintentos. El historial de la ejecución dice cuál.",
        )
        alarma(
            "EjecucionExpirada", "ExecutionsTimedOut",
            f"La tubería diaria de Precio Vivo ({nombre_maquina}) superó su "
            f"tope de una hora. Con el Map de productos el reloj normal ronda "
            f"los 10 minutos, así que esto apunta a un paso colgado, no a "
            f"lentitud.",
        )

        CfnOutput(self, "TemaAlertas", value=tema.topic_arn)
        CfnOutput(self, "CorreoSuscrito", value=correo or "(ninguno: pasa -c correo_alertas=...)")
