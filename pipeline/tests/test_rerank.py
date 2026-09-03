"""Pruebas del reordenamiento por señales estructuradas.

Las dos primeras son REGRESIONES de fallos medidos, no hipótesis: la penalización
de amplitud hundiendo las fichas costó 10 puntos de recall de búsqueda antes de
acotarse, y sin el desempate por id el arnés dejaba de ser reproducible.
"""
from __future__ import annotations

from preciovivo.corpus import (
    TIPO_EVENTO_ANOMALIA,
    TIPO_MERCADO_DIA,
    TIPO_PRODUCTO_PERFIL,
    TIPO_PRODUCTO_PERIODO,
    Chunk,
)
from preciovivo.rerank import reordenar, tipos_pedidos


def chunk(cid: str, tipo: str, slug: str | None, d0: str, d1: str,
          texto: str = "texto") -> Chunk:
    return Chunk(id=cid, tipo=tipo, texto=texto, slug=slug, producto=slug,
                 mercado="GMML", fecha_inicio=d0, fecha_fin=d1)


PERFIL = chunk("perfil:papa", TIPO_PRODUCTO_PERFIL, "papa-blanca",
               "2024-07-01", "2026-08-24", "Ficha de Papa. Estacionalidad.")
SEMANA = chunk("periodo:papa:2025-W08", TIPO_PRODUCTO_PERIODO, "papa-blanca",
               "2025-02-17", "2025-02-21", "Papa semana 2025-W08.")
OTRA = chunk("periodo:cebolla:2025-W08", TIPO_PRODUCTO_PERIODO, "cebolla",
             "2025-02-17", "2025-02-21", "Cebolla semana 2025-W08.")


def _orden(cands, pregunta, slugs=frozenset(), desde=None, hasta=None):
    return [c.id for c, _, _ in reordenar(cands, pregunta, slugs, desde, hasta)]


# --------------------------------------------------------------------------- #
# Regresión: la ficha no puede hundirse por ser ancha
# --------------------------------------------------------------------------- #
def test_la_ficha_no_se_hunde_por_su_amplitud():
    """El fallo que costó 10 puntos de recall de búsqueda.

    Un `producto-perfil` abarca los ~785 días de la serie entera. Penalizarlo
    por amplitud le restaba 1,67 y lo mandaba por debajo de cualquier semana.
    17 de los 23 casos que empeoraron con el reordenador eran fichas.
    """
    cands = [(SEMANA, 1.0), (PERFIL, 0.9)]
    orden = _orden(cands, "¿cuál es el rango histórico de la papa blanca?",
                   frozenset({"papa-blanca"}))
    assert orden[0] == PERFIL.id


def test_la_amplitud_si_desempata_entre_periodos_con_fecha():
    """Acotar la penalización no es quitarla: entre dos periodos y con fecha
    preguntada, el más ajustado sigue ganando."""
    mes = chunk("periodo:papa:2025-02", TIPO_PRODUCTO_PERIODO, "papa-blanca",
                "2025-02-01", "2025-02-28")
    cands = [(mes, 1.0), (SEMANA, 1.0)]
    orden = _orden(cands, "¿qué pasó con la papa en febrero?",
                   frozenset({"papa-blanca"}), "2025-02-17", "2025-02-21")
    assert orden[0] == SEMANA.id


# --------------------------------------------------------------------------- #
# Señales
# --------------------------------------------------------------------------- #
def test_el_producto_preguntado_gana_al_que_no_lo_es():
    cands = [(OTRA, 1.0), (SEMANA, 0.1)]
    orden = _orden(cands, "¿qué pasó con la papa?", frozenset({"papa-blanca"}))
    assert orden[0] == SEMANA.id


def test_cubrir_la_fecha_gana_a_no_cubrirla():
    lejos = chunk("periodo:papa:2024-W30", TIPO_PRODUCTO_PERIODO, "papa-blanca",
                  "2024-07-22", "2024-07-26")
    cands = [(lejos, 1.0), (SEMANA, 0.1)]
    orden = _orden(cands, "¿qué pasó con la papa en febrero de 2025?",
                   frozenset({"papa-blanca"}), "2025-02-01", "2025-02-28")
    assert orden[0] == SEMANA.id


def test_el_desempate_por_id_hace_el_orden_reproducible():
    """Dos chunks idénticos en señales tienen que salir siempre igual.

    Sin esto el arnés daría números distintos entre corridas y el MDE dejaría de
    significar nada.
    """
    a = chunk("aaa", TIPO_PRODUCTO_PERIODO, None, "2025-01-01", "2025-01-05")
    b = chunk("bbb", TIPO_PRODUCTO_PERIODO, None, "2025-01-01", "2025-01-05")
    assert _orden([(b, 1.0), (a, 1.0)], "x") == ["aaa", "bbb"]
    assert _orden([(a, 1.0), (b, 1.0)], "x") == ["aaa", "bbb"]


def test_sin_candidatos_no_revienta():
    assert reordenar([], "lo que sea", frozenset(), None, None) == []


# --------------------------------------------------------------------------- #
# Intención
# --------------------------------------------------------------------------- #
def test_la_intencion_reconoce_los_tipos_principales():
    assert TIPO_PRODUCTO_PERFIL in tipos_pedidos("¿cuál es el rango histórico?")
    assert TIPO_EVENTO_ANOMALIA in tipos_pedidos("¿hubo algo raro con la papa?")
    assert TIPO_MERCADO_DIA in tipos_pedidos("¿qué estuvo más caro ese día?")


def test_una_pregunta_sin_senal_de_tipo_no_inventa_una():
    """Adivinar mal el tipo es peor que no adivinarlo: un tipo equivocado suma
    un punto entero al chunk incorrecto."""
    assert tipos_pedidos("hola") == set()
