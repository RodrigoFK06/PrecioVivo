"""Doble comprobación: ninguna cifra sale sin respaldo en el contexto.

POR QUÉ ESTO ES UN MÓDULO Y NO UN TROZO DEL ARNÉS
-------------------------------------------------
Esta lógica ya existía, pero solo dentro de `evals/run_generacion.py`: medía la
invención de cifras DESPUÉS del hecho, en una corrida de evaluación, sobre 14
casos y solo cuando había `AI_API_KEY`. Es decir, el sistema sabía reconocer una
cifra inventada y aun así la publicaba.

Aquí la misma comprobación se ejecuta EN LÍNEA, sobre cada respuesta, antes de
devolverla. El arnés ahora importa de este módulo en vez de tener su copia: una
sola definición de «cifra sin respaldo» para medir y para actuar. Si divergieran,
la evaluación estaría midiendo un sistema distinto del que responde.

LA REGLA
--------
Cada número que la respuesta afirma tiene que estar en el contexto recuperado, o
derivarse de él por aritmética simple (resta o variación porcentual, que son las
dos operaciones que el prompt pide de forma explícita). Lo que no cumple ninguna
de las dos es invención.

Es determinista y estrecho a propósito, por lo mismo que se descartó el juez LLM:
un juez trae su propia tasa de error, cuesta por llamada y hace que el resultado
deje de ser reproducible. Lo que este producto no puede inventar son CIFRAS, y
las cifras se extraen con una expresión regular.

QUÉ HACE CUANDO ENCUENTRA UNA
-----------------------------
No la corrige: no hay forma fiable de saber cuál era la cifra correcta. Escala
en dos tiempos:

  1. REINTENTO, señalando exactamente qué números no están respaldados. Un
     modelo que se inventó un decimal suele acertar al segundo intento cuando se
     le dice cuál fue.
  2. Si reincide, se DEGRADA a la respuesta determinista. Una respuesta peor y
     verdadera vale más que una buena e inventada, que es la misma razón por la
     que existe el piso determinista en la recuperación.

LO QUE NO MIDE, DICHO CLARO
---------------------------
No sabe si la respuesta es útil, ni si razona bien, ni si atribuye causas que el
dato no respalda. Un modelo puede tragarse una premisa falsa sin escribir una
sola cifra inventada y esto lo dejaría pasar. Cubre un fallo concreto —el más
caro en un producto de precios— y no pretende cubrir más.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tolerancia relativa al comparar una cifra de la respuesta con una derivación.
# 1 % absorbe el redondeo del modelo ("+3,0 %" por 2,999…%) sin dejar pasar
# invenciones: una cifra inventada rara vez cae a menos del 1 % de una
# derivación legítima.
TOLERANCIA = 0.01

# QUÉ NÚMEROS CUENTAN, Y LO QUE COSTÓ ACERTAR
#
# La primera versión de esta regla (en el arnés) descartaba todo valor menor que
# 10 para no ahogarse en ruido ("2 días", "los 5 más baratos"). Descartaba, por
# tanto, TODOS LOS PRECIOS: S/ 1.03, S/ 4.87, un +3,0 %. El arnés reportó 203
# cifras afirmadas y cero invenciones, y ese cero no significaba nada — solo
# había mirado toneladas y fechas.
#
# Regla actual: cuentan los decimales (un precio o un porcentaje casi nunca es
# entero redondo) y los enteros >= 10. Se descartan los años y los enteros de un
# dígito, que suelen ser conteos del propio texto y no datos.
#
# Límite conocido y aceptado: un precio entero inventado ("S/ 5") se escaparía.
# En esta serie los precios llevan decimales, así que el hueco es estrecho — pero
# existe y queda dicho.
_ANIOS = {float(a) for a in range(2000, 2100)}

_PRECIO = re.compile(r"S/\s*\d|\d+(?:[.,]\d+)?\s*soles", re.IGNORECASE)


def numeros(texto: str) -> set[float]:
    """Los números de un texto, normalizados.

    Maneja las tres formas que conviven en este dominio: `1.03`, `1,03` y
    `2 355` con espacio de millares. El orden de las sustituciones importa —
    primero se quitan los separadores de millar, luego se unifica el decimal.
    """
    limpio = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", texto or "")
    fuera: set[float] = set()
    for bruto in re.findall(r"-?\d+(?:[.,]\d+)?", limpio):
        try:
            v = abs(float(bruto.replace(",", ".")))
        except ValueError:
            continue
        if v in _ANIOS:
            continue
        if "." not in bruto and "," not in bruto and v < 10:
            continue
        fuera.add(round(v, 4))
    return fuera


def derivable(x: float, base: set[float]) -> bool:
    """¿Sale `x` de dos números del contexto por aritmética simple?

    Se cubren las dos operaciones que el prompt pide de forma explícita
    —describir un movimiento y su magnitud— y ninguna más. Ampliar el repertorio
    haría más fácil «justificar» cualquier número, que es justo lo contrario de
    lo que esto busca.
    """
    def cerca(a: float, b: float) -> bool:
        return abs(a - b) <= max(TOLERANCIA * max(abs(b), 1.0), 0.005)

    for a in base:
        for b in base:
            if a == b:
                continue
            if cerca(x, abs(a - b)):
                return True
            if b and cerca(x, abs(100.0 * (a - b) / b)):
                return True
    return False


def clasificar_cifras(respuesta: str, contexto: str) -> dict:
    """Reparte las cifras de la respuesta en las tres categorías.

    Son TRES y no dos porque colapsarlas escondería la incertidumbre: `derivada`
    es aritmética legítima sobre el dato y tratarla como error castigaría justo
    lo que se quiere que el modelo haga.
    """
    del_contexto = numeros(contexto)
    en_contexto, derivadas, sin_respaldo = [], [], []
    for x in sorted(numeros(respuesta)):
        if any(abs(x - c) <= 0.005 for c in del_contexto):
            en_contexto.append(x)
        elif derivable(x, del_contexto):
            derivadas.append(x)
        else:
            sin_respaldo.append(x)
    return {"en_contexto": en_contexto, "derivadas": derivadas,
            "sin_respaldo": sin_respaldo}


def afirma_un_precio(respuesta: str) -> bool:
    """¿La respuesta pone precio a algo?

    Se usa en los casos de abstención. Una respuesta correcta a «¿cuánto cuesta
    la papaya?» explica que no está en el catálogo; una incorrecta responde con
    el precio de la papa por parecido de nombre.
    """
    return bool(_PRECIO.search(respuesta or ""))


@dataclass(frozen=True)
class Veredicto:
    """Resultado de la doble comprobación sobre una respuesta."""
    ok: bool
    en_contexto: list[float] = field(default_factory=list)
    derivadas: list[float] = field(default_factory=list)
    sin_respaldo: list[float] = field(default_factory=list)

    @property
    def motivo(self) -> str | None:
        if self.ok:
            return None
        return ("cifras sin respaldo en el contexto: "
                + ", ".join(f"{x:g}" for x in self.sin_respaldo))


def verificar(respuesta: str, contexto: str) -> Veredicto:
    """La comprobación completa sobre una respuesta ya generada."""
    c = clasificar_cifras(respuesta, contexto)
    return Veredicto(ok=not c["sin_respaldo"], **c)


def instruccion_de_correccion(v: Veredicto) -> str:
    """Qué decirle al modelo en el reintento.

    Se le nombran las cifras exactas en vez de repetir la regla general: «no
    inventes cifras» ya estaba en el prompt del sistema y no bastó. Señalar el
    número concreto convierte una regla abstracta en una corrección accionable.
    """
    lista = ", ".join(f"{x:g}" for x in v.sin_respaldo)
    return (
        f"CORRECCIÓN. Tu respuesta anterior afirmaba estas cifras que NO están "
        f"en el contexto ni se derivan de él: {lista}. Reescríbela usando "
        f"únicamente números que aparezcan literalmente en el contexto. Si el "
        f"dato que querías dar no está, di que no lo tienes en vez de estimarlo."
    )
