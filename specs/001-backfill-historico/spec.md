# Feature Specification: Profundidad histórica del catálogo GMML

**Feature Branch**: `001-backfill-historico`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "escalar la cantidad de datos sin coste; la fuente publica desde
noviembre de 2019 y solo se está usando desde julio de 2024"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - La promesa «con más historia ganará» se resuelve (Priority: P1)

El sistema publica hoy un veredicto honesto: evalúa cuatro familias de modelos
sobre los 72 productos, el modelo de árboles pierde contra la estructura
autorregresiva simple, y lo dice. Pero añade una frase que NO ha medido: *«se
espera que lo haga con más data»*.

Esa frase es la única afirmación del sistema que ningún cálculo respalda. Ampliar
la historia la convierte en un resultado: o el modelo de árboles gana y la frase
se cumple, o no gana y la frase se retira y se sustituye por lo que se midió.

**Why this priority**: es lo único de esta funcionalidad que cambia lo que el
producto puede *afirmar*. El resto mejora la experiencia; esto retira la última
promesa sin respaldo, que es lo que el proyecto vende.

**Independent Test**: se ejecuta la misma comparación de modelos sobre la
historia ampliada y se comparan sus errores con los de hoy, producto a producto y
por horizonte. El criterio de éxito es que el veredicto pase a citar una
medición; NO que un modelo concreto gane.

**Acceptance Scenarios**:

1. **Given** el catálogo con la historia ampliada, **When** se ejecuta la
   comparación de modelos, **Then** el veredicto de cada horizonte declara el
   error de cada familia, sobre cuántos productos se calculó, y la historia media
   por producto con la que se calculó.
2. **Given** el veredicto nuevo, **When** el modelo de árboles sigue perdiendo,
   **Then** el sistema retira la frase «se espera que gane con más historia» y la
   sustituye por el resultado medido con la historia ampliada.
3. **Given** ambas corridas, **When** se comparan, **Then** el sistema reporta el
   cambio del error de cada familia entre la historia corta y la larga, sobre el
   MISMO conjunto de productos.

---

### User Story 2 - Preguntar por un periodo antiguo devuelve el dato, no una disculpa (Priority: P2)

Una persona pregunta por lo que pasó con un producto en una fecha anterior a la
cobertura actual. Hoy no hay nada que recuperar y la respuesta correcta es decir
que no se tiene el dato. Con la historia ampliada, la respuesta es el dato.

**Why this priority**: amplía lo que el asistente puede responder sin cambiar
cómo responde. Depende de la P1 solo en que comparten la ingesta.

**Independent Test**: se pregunta por un periodo dentro del rango nuevo y se
comprueba que la respuesta cita cifras trazables al contexto recuperado, sin
inventar ninguna.

**Acceptance Scenarios**:

1. **Given** una pregunta sobre un periodo dentro del rango ampliado, **When** se
   responde, **Then** toda cifra afirmada aparece en el contexto recuperado o se
   deriva de él.
2. **Given** una pregunta sobre un periodo ANTERIOR al rango disponible, **When**
   se responde, **Then** el sistema declara que no dispone del dato en vez de
   ofrecer el del periodo más cercano.

---

### User Story 3 - La estacionalidad se puede afirmar, no intuir (Priority: P3)

Con varios años completos, «este producto sube todos los agostos» deja de ser una
impresión y pasa a ser una observación repetida sobre ciclos distintos.

**Why this priority**: es la consecuencia más interesante a medio plazo, pero no
requiere trabajo propio: sale de las dos anteriores.

**Independent Test**: se consulta un producto con estacionalidad conocida y se
comprueba que la evidencia recuperada abarca más de un ciclo anual.

**Acceptance Scenarios**:

1. **Given** un producto con varios años de historia, **When** se pregunta por su
   comportamiento estacional, **Then** la evidencia recuperada incluye el mismo
   periodo de al menos dos años distintos.

---

### Edge Cases

- **Un día publicado con formato distinto al actual.** La fuente lleva años
  publicando; el formato del documento puede haber cambiado. La compuerta de
  escritura existente debe bloquear la carga de un día estructuralmente roto sin
  detener la carga de los demás.
- **Un día ausente en la fuente.** Un feriado, una huelga o un fallo del emisor
  dejan huecos. Un hueco NO es un fallo del sistema y no debe reportarse como tal.
- **El mismo día ingestado dos veces.** La carga debe ser idempotente: reingestar
  un periodo ya cargado no puede duplicar ni alterar lo existente.
- **Un producto que existió y dejó de publicarse.** Su serie termina; el sistema
  no debe extrapolarla ni tratarla como dato faltante de hoy.
- **Un producto cuyo nombre cambió a lo largo de los años.** Dos nombres para la
  misma cosa producen dos series cortas en vez de una larga. Debe detectarse y
  reportarse, aunque su resolución quede fuera de alcance.
- **La ampliación excede el presupuesto de indexación disponible.** El proceso
  debe detenerse ANTES de consumir cuota, no a mitad.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE ingestar la historia disponible en la fuente desde
  su publicación más antigua, no solo desde la cobertura actual.
- **FR-002**: La ingesta DEBE ser idempotente: reingestar un periodo ya cargado
  no altera los datos existentes.
- **FR-003**: Un día cuyo documento no se pueda interpretar DEBE bloquearse sin
  impedir la carga de los demás días del mismo lote, y DEBE quedar contabilizado
  en el resultado de la ejecución.
- **FR-004**: El proceso DEBE informar, antes de consumir presupuesto de
  indexación, cuánto va a consumir; y DEBE detenerse si supera el límite
  configurado.
- **FR-005**: El sistema DEBE seguir publicando el dato del día durante la
  ampliación: el backfill no puede bloquear la operación diaria.
- **FR-006**: La comparación de modelos DEBE declarar sobre cuántos productos y
  cuántas observaciones se calculó cada veredicto.
- **FR-007**: El sistema DEBE detectar y reportar productos cuya serie aparezca
  fragmentada por un cambio de nombre en la fuente.
- **FR-008**: El coste de la ampliación DEBE quedar registrado y comparado contra
  la estimación previa.

### Key Entities

- **Día de mercado**: una jornada publicada por la fuente, con su fecha, su
  conjunto de productos y los precios y volúmenes de cada uno. Es la unidad
  atómica de la ingesta y la que la compuerta de escritura valida.
- **Serie de producto**: la sucesión de días de un mismo producto en un mismo
  mercado. Es la unidad que consume el modelo, y su longitud es lo que esta
  funcionalidad amplía.
- **Presupuesto de indexación**: la cantidad de trabajo de indexación disponible
  antes de agotar el recurso externo que la provee. Es finito y no renovable sin
  gasto.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La cobertura temporal del catálogo pasa de 529 a al menos 1.500 días
  de mercado con precios (la fuente publica 1.667 entre 2019-11-04 y hoy; se
  admite perder hasta un 10 % en días no interpretables, siempre que queden
  contados y nombrados).
- **SC-002**: La comparación de modelos se recalcula sobre la historia ampliada y
  publica, para cada horizonte, el error de las cuatro familias y el cambio
  respecto a la medición de hoy (h=1: baseline 0,2074 · AR(1) 0,1587 · volumen
  0,1648 · árboles 0,1795 — h=7: 0,5318 · 0,4744 · 0,4980 · 0,4927).
- **SC-003**: El veredicto deja de contener la expresión «se espera que gane con
  más historia»: o el modelo de árboles gana y se dice, o no gana y se publica
  cuánta historia hizo falta para descartarlo.
- **SC-004**: El coste en dinero de la ampliación es 0, y el consumo del
  presupuesto de indexación queda por debajo del 40 % del disponible.
- **SC-005**: Durante toda la ampliación, la publicación diaria no se interrumpe
  ningún día hábil.
- **SC-006**: Ninguna respuesta del asistente sobre los periodos nuevos afirma una
  cifra ausente del contexto recuperado.

## Assumptions

- La fuente sigue publicando en el mismo lugar y con la misma organización con la
  que se navega hoy; los documentos antiguos siguen accesibles.
- El formato del documento ha podido cambiar a lo largo de los años. Se asume que
  la compuerta de escritura existente detecta los días no interpretables, y que la
  proporción de días perdidos es aceptable si queda reportada.
- La ampliación se hace UNA vez. La operación diaria seguida no cambia.
- No se amplía la historia de los mercados secundarios: solo publican su último
  día y no hay archivo histórico que recuperar.
- El presupuesto de indexación disponible es finito y compartido con la operación
  diaria; la ampliación no puede dejarlo a cero.
- No se promete que un modelo concreto gane. Se promete que la comparación pase
  de inconclusa a concluyente.
