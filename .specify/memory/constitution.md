<!--
SYNC IMPACT REPORT
Version change: (sin constitución previa) → 1.0.0
Modified principles: ninguno (documento inicial)
Added sections:
  - Core Principles I-V
  - Restricciones Técnicas
  - Flujo de Trabajo y Puertas de Calidad
  - Governance
Removed sections: ninguna
Follow-up TODOs: ninguno — todos los marcadores de la plantilla quedan resueltos.

Nota de origen: los cinco principios NO son aspiracionales. Cada uno se deriva de
un fallo real y documentado de este repositorio; las referencias apuntan a
docs/postmortem-fallos-silenciosos.md y docs/aws.md.
-->

# Precio Vivo Constitution

## Core Principles

### I. Nada se afirma sin calcularse (NO NEGOCIABLE)

Ningún número que el sistema publique —en el sitio, en el README, en un commit o
en una decisión de arquitectura— puede ser una estimación presentada como
medición.

Reglas:

- Toda cifra en documentación DEBE poder reproducirse con un comando concreto.
- Toda decisión de arquitectura DEBE citar el número que la justifica, no una
  intuición. «Lambda arranca rápido» no vale; «Init 707,5 ms contra p50 1,78 ms,
  397×» sí.
- Una estimación se etiqueta como estimación. Si se midió después y no coincide,
  se corrige el documento y se dice que la estimación era mía.

Rationale: la estimación de 25 s por producto salió de una máquina de escritorio;
en Lambda son 39,17 s. La diferencia no cambió la decisión, pero sí habría
cambiado el presupuesto si nadie la hubiera vuelto a medir.

### II. Un fallo que no deja rastro no existe (NO NEGOCIABLE)

Degradar con elegancia de cara al usuario es correcto y DEBE mantenerse. Pero
exige su contrapartida: gritar hacia dentro.

Reglas:

- PROHIBIDO el `catch` vacío. Todo bloque que capture una excepción para no
  romper la respuesta DEBE registrar el nombre y el mensaje del error.
- Un proceso que hace algo mal DEBE devolver un código de salida distinto de
  cero, o dejar en su resultado un campo que lo delate.
- Un valor por defecto que no describe el despliegue real está PROHIBIDO: es una
  trampa esperando a que falte una variable.
- «Visible en una consola» no es notificado. Lo que debe despertar a alguien
  DEBE llegar a un buzón.

Rationale: nueve fallos en tres días compartieron esta forma. Ocho los encontró
una persona mirando un número que no cuadraba; ninguno habría sobrevivido diez
minutos si hubiera devuelto un código distinto de cero.

### III. Todo guard se prueba contra un fallo conocido (NO NEGOCIABLE)

Un indicador que siempre sale perfecto es indistinguible de uno roto.

Reglas:

- Todo test, alarma, umbral o detector nuevo DEBE verificarse enseñándole un caso
  que TIENE que marcar, antes de confiar en su resultado.
- Un guard que depende de un archivo no versionado no es un guard.
- Un guard que falla solo cuando no pasa nada se corrige o se retira: uno que
  cría lobos deja de mirarse.

Rationale: el detector de invención de cifras reportó «203 cifras, 0
invenciones». Descartaba todo número menor que 10 —todos los precios— y solo
había mirado toneladas y fechas. Se descubrió dándole seis invenciones conocidas:
falló cuatro.

### IV. La honestidad del modelo por encima de su lucimiento (NO NEGOCIABLE)

Este producto vende precios, no promesas. Un dato inventado con formato impecable
es peor que la ausencia del dato.

Reglas:

- El anti-leakage del forecast —walk-forward de ventana expansiva, re-ajuste del
  GBM por origen, forward-fill causal del volumen— NO se toca sin verificar que
  la salida es idéntica byte a byte sobre los datos reales.
- El kill-gate DEBE publicar cuándo el modelo pierde. Si el baseline gana, se
  dice.
- Los pronósticos se etiquetan como estimaciones, nunca como cifras del reporte.
- La respuesta a una pregunta sobre algo que no está en el catálogo DEBE ser una
  abstención, nunca el dato de un producto de nombre parecido.
- Mercados distintos NO se comparan ni se fusionan. Precio de cierre y promedio
  semanal son magnitudes distintas aunque compartan nombre.

Rationale: con 537 días de historia el GBM se evalúa y pierde contra AR(1)
(0,1711 contra 0,1476). Publicarlo es lo que hace creíbles los demás números.

### V. Permisos mínimos y coste conocido

Cada permiso concedido y cada dólar gastado DEBEN tener un motivo escrito.

Reglas:

- PROHIBIDOS `Action: "*"` y `Resource: "*"`. Si un comodín parece la única
  salida, se para y se explica antes de concederlo.
- PROHIBIDOS los atajos de CDK (`grant_read`, `grant_write`): conceden familias
  enteras de acciones.
- Los secretos NO se gestionan desde la infraestructura como código: acaban en el
  repositorio, en el historial de CloudFormation y en los logs de despliegue.
- Todo componente DEBE tener su coste mensual estimado a volumen actual y a ×100,
  y su techo medido: dónde deja de funcionar y por qué.

Rationale: el único punto donde un comodín KMS parecía inevitable resultó
innecesario al comprobarlo. No se comprueba lo que se da por supuesto.

## Restricciones Técnicas

**Infraestructura.** Todo en CDK/Python. Ni un recurso creado desde la consola.
Si algo no se puede hacer en CDK, se dice en vez de hacerlo a mano.

**Sin VPC, sin NAT Gateway, sin EC2, sin capacidad aprovisionada.** Nada que
cobre estando ocioso. Si un diseño parece necesitar VPC, se para y se explica por
qué antes de introducirla.

**Gasto cero es un requisito, no un objetivo.** Cualquier componente que salga de
la capa gratuita necesita aprobación explícita. Los servicios sin capa gratuita
—Bedrock— se dejan escritos y apagados hasta que haya presupuesto.

**Fuentes secundarias no bloquean.** SISAP y el boletín multi-mercado enriquecen
el dato; su fallo NO puede impedir que se publique el reporte 335 del día.

**Techos, medidos y vigentes:**

| techo | valor | dónde revienta |
|---|---|---|
| Snapshot en memoria | 3,5 MB | ~15 MB: migrar a acceso por clave |
| Estado de Step Functions | 36,6 KB de 256 KB | 510 productos |
| Concurrencia de la cuenta | 8 de 10 | cuota no ajustable sin solicitud |
| Cuota de Jina | 0,50 %/corrida | ~9 meses al ritmo actual |
| Paquete de Lambda | 207,6 MB | 250 MB sin comprimir |

## Flujo de Trabajo y Puertas de Calidad

**CI es obligatorio y bloquea.** Cuatro trabajos independientes —pipeline, sitio,
evaluaciones y dead-man's-switch— para que un fallo del sitio no oculte uno del
pipeline. `infra` y `scripts` se lintean igual que el resto.

**Las evaluaciones tienen umbral.** Recuperación: recall mínimo 0,9. Generación:
tasa de invención de cifras 0,0 — sin margen, porque la promesa del prompt es
«nunca inventes cifras» y una promesa con tolerancia no es una promesa.

**Guards sobre el artefacto, no solo sobre el código.** El índice publicado es
sujeto de prueba: ningún test unitario lo mira.

**Verificar el efecto, no el código de salida.** Un despliegue que devuelve 0 no
prueba que se aplicó; se comprueba el estado resultante.

**Lo que no está hecho se marca como no hecho.** Prohibido hardcodear para que
parezca que funciona.

## Governance

Esta constitución prevalece sobre cualquier práctica no escrita. Cuando una
decisión la contradiga, se cambia la decisión o se enmienda la constitución —
nunca se ignora en silencio, que sería el Principio II aplicado al propio
documento.

**Enmiendas.** Toda modificación DEBE llevar Sync Impact Report al inicio del
archivo, incremento de versión y fecha. Un principio nuevo o materialmente
ampliado es MINOR; una eliminación o redefinición incompatible es MAJOR; una
aclaración es PATCH.

**Cumplimiento.** Toda revisión verifica que los cinco principios se respetan. La
complejidad se justifica o se retira. Un principio que se incumple tres veces
seguidas sin que nadie lo note es señal de que el principio está mal escrito, no
de que el equipo sea indisciplinado: se reescribe.

**Guía de ejecución.** `docs/estado-tecnico.md` para el estado medido del
sistema, `docs/aws.md` para las decisiones de infraestructura con su alternativa
descartada, y `docs/postmortem-fallos-silenciosos.md` para el catálogo de fallos
que estos principios existen para evitar.

**Version**: 1.0.0 | **Ratified**: 2026-08-25 | **Last Amended**: 2026-08-25
