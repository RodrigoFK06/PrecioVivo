# Contrato · la compuerta de formato del parser

El contrato interno que más importa de esta feature, porque hoy **está roto en
silencio** y todo lo demás depende de él.

## Estado actual, medido

`parse_report(pdf)` devuelve un `ParseResult` con `layout_changed: bool`. Ese
booleano es la compuerta: la máquina de estados deriva a `Fail` cuando es `True`.

Sobre una edición de enero de 2023:

```
filas          : 72
con precio     : 0
warnings       : 144
layout_changed : False     <-- la compuerta la aprueba
```

`layout_fingerprint` compara el **orden de los anclajes del encabezado**. Ese
orden no cambió en siete años (`productos|masa|unidad|precios|equiv|ultimos`,
idéntico en 2019 y 2026). Lo que cambia es la **geometría de las columnas**, y la
compuerta no la mira.

## Contrato nuevo

`ParseResult` gana tres campos. Ninguno existente cambia de tipo ni de
significado — el resto del pipeline no se entera.

| campo | tipo | qué es |
|---|---|---|
| `ancho_pagina` | `float` | ancho en puntos de la primera página |
| `escala` | `float` | `ancho_pagina / 595.2` |
| `filas_fallidas` | `int` | filas sin unidad **o** sin `precio_hoy_kg` |

Y `layout_changed` pasa a ser `True` cuando se cumple **cualquiera** de:

1. la huella del encabezado no coincide *(comportamiento actual, sin cambios)*;
2. `filas_fallidas / len(rows) > 0,20` *(V4)*;
3. `len(rows) == 0`.

### Normalización

Los umbrales de columna —`NAME_MAX`, `MASA_MIN`, `MASA_MAX`, `MASA_BANDS`,
`HEADER_BAND`— se multiplican por `escala` antes de usarse.

**Verificado**: con la normalización, la edición de 2023 pasa de **0/72 con 144
warnings** a **72/72 con 0 warnings**.

**Verificado también lo que NO arregla**: las ediciones de 2019 siguen en 36/72.
Su causa es otra —la unidad va pegada a la última masa, `':Atado'`,
`'43Kilogramo'`— y queda fuera de alcance. Con el contrato nuevo esos días se
**bloquean y se cuentan** en vez de entrar a medias.

## Invariantes que la implementación debe preservar

Estos no son deseos: son las reglas que el Principio IV declara intocables.

1. `layout_fingerprint` **no cambia**. La huella publicada de referencia sigue
   siendo válida y las 29 ediciones verificadas de 2024-2026 siguen dando la
   misma.
2. Sobre una página de 595,2 pt, `escala == 1,0` y todos los umbrales quedan
   **numéricamente idénticos** a los de hoy.
3. La salida sobre las ediciones actuales es **byte a byte la misma**. Es
   condición de aceptación de T2, no una comprobación opcional.

## Pruebas obligatorias (Principio III)

Un guard nuevo se prueba enseñándole algo que TIENE que marcar.

Los fixtures no se versionan (regla legal §0.5); los trae
`pipeline/tests/fixtures/pdfs_epocas/traer.py`, que verifica el sha256 de cada
uno. Si la fuente republica un documento, el script lo dice en vez de bajar otra
cosa en silencio. Sin fixtures, las pruebas hacen `skip` — el patrón ya usado en
`test_parser*.py`.

| fixture | qué debe pasar | por qué |
|---|---|---|
| 2023, 661,1 pt | `72/72` precios, `layout_changed == False` | hoy da 0/72 en verde |
| 2019, 612,0 pt | `layout_changed == True` | hoy entra al 50 % sin avisar |
| 2026, 595,2 pt | salida idéntica a la de hoy | no romper lo que funciona |
| 2021, 612,0 pt | salida idéntica a la de hoy | ancho ≠ 595 que YA parseaba bien |

La cuarta es la que evita el falso arreglo: 2021 viene en 612 pt y hoy parsea al
100 %. Si la normalización rompiera ese caso, estaría corrigiendo el síntoma
equivocado.
