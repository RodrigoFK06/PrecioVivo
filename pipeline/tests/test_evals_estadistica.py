"""Pruebas de `evals/estadistica.py`.

Cada una compara contra un valor de referencia CONOCIDO, no contra la salida de
la propia funcion. Una prueba que solo comprueba que el codigo no explota no
distingue una formula correcta de una equivocada.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from estadistica import (  # noqa: E402
    diagnostico,
    linea_mde,
    margen_al_techo,
    mde_pareado,
    wilson,
)


# --------------------------------------------------------------------------- #
# Wilson
# --------------------------------------------------------------------------- #
def test_wilson_reproduce_un_valor_publicado():
    """El ejemplo canonico: 5 exitos de 10, IC95 de Wilson.

    Valor de referencia calculado a mano con la formula cerrada:
    centro 0,5; medio 0,2839. Si alguien reescribe la funcion y se le va un
    termino, esto lo caza.
    """
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=5e-4)
    assert hi == pytest.approx(0.7634, abs=5e-4)


def test_wilson_no_colapsa_cuando_la_proporcion_es_uno():
    """La razon de usar Wilson y no Wald.

    Con p=1, Wald da un intervalo de ancho CERO: [1,0, 1,0]. Eso afirma certeza
    absoluta a partir de una muestra finita, que es exactamente la mentira que
    este proyecto persigue. Wilson deja el limite inferior por debajo de 1.
    """
    lo, hi = wilson(58, 58)
    assert hi == pytest.approx(1.0)  # el techo, salvo epsilon de coma flotante
    assert lo < 1.0
    assert lo == pytest.approx(0.938, abs=0.01)


def test_wilson_nunca_se_sale_del_intervalo_unidad():
    for exitos, n in ((0, 5), (5, 5), (1, 3), (0, 1), (1, 1)):
        lo, hi = wilson(exitos, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_se_estrecha_al_crecer_la_muestra():
    anchos = [wilson(0.9 * n, n)[1] - wilson(0.9 * n, n)[0] for n in (10, 100, 1000)]
    assert anchos[0] > anchos[1] > anchos[2]


def test_wilson_con_n_cero_no_afirma_nada():
    assert wilson(0, 0) == (0.0, 1.0)


# --------------------------------------------------------------------------- #
# MDE pareado
# --------------------------------------------------------------------------- #
def test_mde_pareado_da_el_numero_que_se_reporta():
    """58 casos con 5 % de discordancia detectan ~8,2 puntos.

    (1,95996 + 0,84162) * raiz(0,05/58) = 0,0823. Es la cifra que el README
    declara, asi que tiene que salir de aqui y no de un redondeo a mano.
    """
    assert mde_pareado(58, 0.05) == pytest.approx(0.0823, abs=5e-4)
    assert mde_pareado(58, 0.10) == pytest.approx(0.1164, abs=5e-4)
    assert mde_pareado(58, 0.20) == pytest.approx(0.1645, abs=5e-4)


def test_mas_casos_detectan_diferencias_mas_pequenas():
    assert mde_pareado(600, 0.05) < mde_pareado(58, 0.05)


def test_mas_discordancia_exige_mas_diferencia():
    assert mde_pareado(58, 0.20) > mde_pareado(58, 0.05)


def test_sin_discordancia_no_se_puede_detectar_nada():
    """Si las dos configuraciones nunca difieren, no hay nada que medir."""
    assert mde_pareado(58, 0.0) == 1.0


# --------------------------------------------------------------------------- #
# El diagnostico, que es lo que de verdad importa
# --------------------------------------------------------------------------- #
def test_una_metrica_saturada_se_declara_inservible():
    """El caso real: recall@8 = 0,991 sobre 58 casos.

    Quedan 0,009 de margen al techo y el MDE mas optimista es 0,082. La metrica
    no puede demostrar una mejora aunque exista.
    """
    d = diagnostico(0.991, 58)
    assert d["margen_al_techo"] == pytest.approx(0.009, abs=1e-3)
    assert min(d["mde"].values()) > d["margen_al_techo"]
    assert d["sirve"] is False


def test_una_metrica_con_recorrido_se_declara_util():
    """La otra cara: 'solo lo recuperado' = 0,750, con 25 puntos de margen."""
    d = diagnostico(0.750, 58)
    assert d["margen_al_techo"] == pytest.approx(0.250, abs=1e-3)
    assert d["sirve"] is True


def test_el_aviso_de_saturacion_aparece_en_el_texto():
    lineas = "\n".join(linea_mde("recall@k", diagnostico(0.991, 58)))
    assert "SATURADA" in lineas
    assert "NO puede demostrar" in lineas


def test_una_metrica_util_no_lleva_el_aviso():
    lineas = "\n".join(linea_mde("solo lo recuperado", diagnostico(0.750, 58)))
    assert "SATURADA" not in lineas


def test_margen_al_techo_nunca_es_negativo():
    assert margen_al_techo(1.0) == 0.0
    assert margen_al_techo(1.5) == 0.0
