"""Reordenamiento de candidatos por señales ESTRUCTURADAS.

POR QUÉ NO UN CROSS-ENCODER
---------------------------
Lo habitual es reordenar con un cross-encoder: un modelo que lee (pregunta,
chunk) y devuelve una relevancia. Se descartó por lo mismo que se descartó el
juez LLM en `evals/run_generacion.py` — CI corre sin red, sin claves y tiene que
dar el mismo número dos veces — y por algo propio de este dominio:

    la pregunta se PARSEA con fiabilidad.

Con 73 nombres canónicos y fechas en español, `retrieval.parsear` sabe de qué
producto y de qué periodo habla la pregunta. Un cross-encoder tendría que INFERIR
por semejanza lo que aquí ya se sabe con certeza. Reordenar por señales que se
conocen exactamente le gana a estimarlas, y además se puede explicar por qué
subió cada chunk, que en un producto de precios no es un lujo.

Queda dicho lo que se pierde: esto no entiende paráfrasis ni intención sutil. Si
un día la pregunta deja de parsearse bien —otro dominio, catálogo abierto— esta
pieza deja de ser la correcta y toca el modelo.

LAS SEÑALES
-----------
Sobre cada candidato, contra la `Consulta` ya parseada:

    slug_pedido      el chunk es del producto que la pregunta nombra
    cubre_fecha      el rango del chunk contiene la fecha pedida
    distancia        días entre el chunk y la fecha pedida (si no la cubre)
    tipo_esperado    el tipo de chunk corresponde a lo que la pregunta busca
    span             días que abarca el chunk; menos es mejor
    lexico           solapamiento de términos con la pregunta
    prior            la posición que traía de la fusión RRF

CUIDADO AL LEER LA MEJORA
-------------------------
El gold set declara sus predicados como {slug, tipo, cubre_fecha}, que son tres
de las señales de aquí. Parte de la mejora es DEFINICIONAL: se reordena por lo
mismo que se mide. No es circular en el sentido dañino —un chunk del producto
preguntado que cubre la fecha preguntada es el correcto también para una persona—
pero la prueba honesta de que sirve son `span` y `precision@k`, que miden
cantidades distintas y que esta pieza no puede satisfacer por construcción.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .corpus import (
    TIPO_EVENTO_ANOMALIA,
    TIPO_MERCADO_DIA,
    TIPO_OTRO_MERCADO,
    TIPO_PRODUCTO_PERFIL,
    TIPO_PRODUCTO_PERIODO,
    Chunk,
)

# --------------------------------------------------------------------------- #
# Pesos
# --------------------------------------------------------------------------- #
# Puestos por razonamiento sobre qué importa, NO por búsqueda sobre el gold set:
# con 148 casos y un MDE de 0,051, ajustar pesos contra el propio conjunto de
# evaluación produciría una mejora que no se sostendría fuera. El orden de
# magnitud es lo que importa y es defendible sin medir:
#
#   producto correcto  >  periodo correcto  >  tipo correcto  >  desempates
#
# Un chunk del producto equivocado no se salva por cubrir la fecha; uno del
# producto correcto que cubre la fecha gana a uno del producto correcto que no.
W_SLUG = 3.0
W_FECHA = 2.0
W_TIPO = 1.0
W_DISTANCIA = 0.6      # penaliza log(1+días) de separación
# SOLO se aplica a `producto-periodo`, y SOLO si la pregunta trae fecha. Una
# ficha abarca los ~785 días de la serie entera: penalizarla por amplitud le
# restaba 1,67 y la hundía por debajo de los periodos semanales. Costó 10 puntos
# de recall de búsqueda y 17 de los 23 casos que empeoraron eran fichas. Un
# perfil no es evidencia diluida, es otra clase de objeto — y el propio arnés ya
# lo dice: solo mide `span` sobre predicados con `cubre_fecha`.
W_SPAN = 0.25          # penaliza log(1+días) de amplitud: evidencia ajustada
W_LEXICO = 0.8
W_PRIOR = 0.5          # la fusión RRF entra como prior, no se tira

_ACENTOS = re.compile(r"[̀-ͯ]")


def _plano(s: str) -> str:
    return _ACENTOS.sub("", unicodedata.normalize("NFKD", s or "")).lower()


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", _plano(s)) if len(t) > 2}


# --------------------------------------------------------------------------- #
# Intención: qué TIPO de chunk pide la pregunta
# --------------------------------------------------------------------------- #
# Cada patrón es una forma real de preguntar, no una categoría teórica. El mapeo
# es a propósito incompleto: si nada dispara, no se premia ningún tipo y el resto
# de señales decide. Adivinar mal el tipo es peor que no adivinarlo.
#
# SIN `` DE CIERRE. Con él, las alternativas que son PREFIJOS —«historic»,
# «anomal», «equival»— no disparaban nunca: exigían que la palabra terminara ahí
# y «historico» sigue con una «o». Media tabla estaba muerta y no se notaba
# porque el reordenador seguía funcionando con el resto de señales.
_INTENCION = (
    (re.compile(r"\b(ficha|historic|rango historic|minimo|maximo|promedio|"
                r"estacional|que mes|suele estar|historia|equival|unidad|"
                r"saco|atado|jaba|cajon|docena|ciento|paquete|bolsa)"),
     TIPO_PRODUCTO_PERFIL),
    (re.compile(r"\b(anomal|raro|extrano|inusual|se disparo|pico|z-score|"
                r"algo raro)"),
     TIPO_EVENTO_ANOMALIA),
    (re.compile(r"\b(mas caro|mas barato|mas caros|mas baratos|ingreso total|"
                r"del dia|ese dia|resumen|cuantos productos|el mercado)"),
     TIPO_MERCADO_DIA),
    (re.compile(r"\b(fruta|frutas|mmf2|mercado de frutas|pollo|aves)"),
     TIPO_OTRO_MERCADO),
    (re.compile(r"\b(subio|bajo|paso con|que paso|variacion|evolucion|"
                r"esta semana|semana|mes de|en enero|en febrero|en marzo|"
                r"en abril|en mayo|en junio|en julio|en agosto|en setiembre|"
                r"en octubre|en noviembre|en diciembre)"),
     TIPO_PRODUCTO_PERIODO),
)


def tipos_pedidos(pregunta: str) -> set[str]:
    """Tipos de chunk que la forma de la pregunta sugiere. Puede estar vacío."""
    p = _plano(pregunta)
    return {tipo for patron, tipo in _INTENCION if patron.search(p)}


# --------------------------------------------------------------------------- #
# Señales
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Senales:
    """Por qué un chunk subió o bajó. Se expone para poder explicar el orden."""
    slug_pedido: bool
    cubre_fecha: bool
    distancia_dias: int | None
    tipo_esperado: bool
    span_dias: int | None
    penalizar_span: bool
    lexico: float
    prior: float

    def score(self) -> float:
        s = W_PRIOR * self.prior
        if self.slug_pedido:
            s += W_SLUG
        if self.cubre_fecha:
            s += W_FECHA
        elif self.distancia_dias is not None:
            s -= W_DISTANCIA * math.log1p(self.distancia_dias)
        if self.tipo_esperado:
            s += W_TIPO
        if self.span_dias is not None and self.penalizar_span:
            s -= W_SPAN * math.log1p(self.span_dias)
        s += W_LEXICO * self.lexico
        return s


def _dias(a: str, b: str) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (ValueError, TypeError):
        return None


def _span(c: Chunk) -> int | None:
    if not (c.fecha_inicio and c.fecha_fin):
        return None
    d = _dias(c.fecha_inicio, c.fecha_fin)
    return None if d is None else d + 1


def _distancia_a_ventana(c: Chunk, desde: str | None, hasta: str | None) -> int | None:
    """Días entre el chunk y la ventana pedida. 0 si se solapan."""
    if not (c.fecha_inicio and c.fecha_fin) or not (desde or hasta):
        return None
    d0, d1 = desde or hasta, hasta or desde
    if c.fecha_inicio <= d1 and d0 <= c.fecha_fin:
        return 0
    if c.fecha_fin < d0:
        return _dias(c.fecha_fin, d0)
    return _dias(c.fecha_inicio, d1)


def senales(chunk: Chunk, pregunta: str, slugs: frozenset[str],
            desde: str | None, hasta: str | None, tipos: set[str],
            prior: float, toks_pregunta: set[str]) -> Senales:
    dist = _distancia_a_ventana(chunk, desde, hasta)
    toks_chunk = _tokens(chunk.texto)
    lexico = (len(toks_pregunta & toks_chunk) / len(toks_pregunta)
              if toks_pregunta else 0.0)
    return Senales(
        slug_pedido=bool(chunk.slug and chunk.slug in slugs),
        cubre_fecha=dist == 0,
        distancia_dias=dist,
        tipo_esperado=chunk.tipo in tipos if tipos else False,
        span_dias=_span(chunk),
        penalizar_span=(chunk.tipo == TIPO_PRODUCTO_PERIODO
                        and bool(desde or hasta)),
        lexico=lexico,
        prior=prior,
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def reordenar(candidatos: list[tuple[Chunk, float]], pregunta: str,
              slugs: frozenset[str], desde: str | None, hasta: str | None,
              ) -> list[tuple[Chunk, float, Senales]]:
    """Reordena (chunk, score_fusion) por señales estructuradas.

    El `prior` se normaliza a [0,1] sobre los propios candidatos: los scores de
    RRF no tienen escala absoluta, solo orden, así que compararlos entre
    consultas no significaría nada.
    """
    if not candidatos:
        return []
    tipos = tipos_pedidos(pregunta)
    toks = _tokens(pregunta)
    peor = min(s for _, s in candidatos)
    mejor = max(s for _, s in candidatos)
    rango = (mejor - peor) or 1.0

    out = []
    for chunk, score in candidatos:
        sen = senales(chunk, pregunta, slugs, desde, hasta, tipos,
                      (score - peor) / rango, toks)
        out.append((chunk, sen.score(), sen))
    # Desempate por id, igual que en el vectorstore y en la fusión: dos chunks
    # con el mismo score tienen que salir siempre en el mismo orden o el arnés
    # deja de ser reproducible.
    out.sort(key=lambda t: (-t[1], t[0].id))
    return out
