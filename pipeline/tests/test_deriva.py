"""Pruebas de `preciovivo.deriva`.

Contra valores calculados A MANO, no contra la salida de la propia funcion. Un
indice de deriva que siempre sale bajo es indistinguible de uno roto, y la unica
forma de separarlos es darle un desplazamiento cuyo valor se conoce de antemano.
"""
from __future__ import annotations

import pytest

from preciovivo import deriva


# --------------------------------------------------------------------------- #
# El calculo, contra aritmetica conocida
# --------------------------------------------------------------------------- #
def test_psi_reproduce_un_calculo_hecho_a_mano():
    """Cinco bins iguales al 20 %; el actual mueve 10 puntos del primero al ultimo.

        (0,1-0,2)*ln(0,1/0,2) + (0,3-0,2)*ln(0,3/0,2)
      = 0,069315 + 0,040546 = 0,109861

    Se construyen las muestras para que caigan exactamente en esas fracciones.
    """
    ref = [i for b in range(5) for i in [b + 0.5] * 20]          # 20 % por bin
    act = ([0.5] * 10 + [1.5] * 20 + [2.5] * 20 + [3.5] * 20 + [4.5] * 30)
    r = deriva.psi(ref, act, n_bins=5)
    assert r.valor == pytest.approx(0.109861, abs=1e-4)
    assert r.clase == "moderada"


def test_dos_ventanas_identicas_no_tienen_deriva():
    datos = [float(i % 37) for i in range(500)]
    r = deriva.psi(datos, list(datos), n_bins=10)
    assert r.valor == pytest.approx(0.0, abs=1e-9)
    assert r.clase == "estable"


def test_un_desplazamiento_grande_pero_solapado_es_significativo():
    """+150 sobre un rango de 1000: un solo bin vacio de diez."""
    ref = [float(i) for i in range(1000)]
    act = [float(i) + 150 for i in range(1000)]
    r = deriva.psi(ref, act, n_bins=10)
    assert r.valor >= deriva.UMBRAL_SIGNIFICATIVA
    assert r.clase == "significativa"


def test_una_deriva_tan_grande_que_no_se_solapa_se_declara_indecidible():
    """El limite honesto del indice, y hay que decirlo en los dos sentidos.

    +700 sobre un rango de 1000 es deriva REAL y enorme, pero deja 7 de 10 bins
    vacios: el mismo sintoma que el artefacto de ventanas mal cortadas. Con los
    numeros a la vista, el indice no puede separar los dos casos.

    "degenerada" no significa "no hay deriva". Significa que este indice no puede
    decidirlo. Llamarlo significativa seria tan falso como llamarlo estable.
    """
    ref = [float(i) for i in range(1000)]
    act = [float(i) + 700 for i in range(1000)]
    r = deriva.psi(ref, act, n_bins=10)
    assert r.valor > deriva.UMBRAL_SIGNIFICATIVA
    assert r.clase == "degenerada"
    assert "mirar los datos" in str(r)


def test_el_indice_crece_con_el_tamano_del_desplazamiento():
    ref = [float(i) for i in range(1000)]
    valores = [deriva.psi(ref, [x + d for x in ref], n_bins=10).valor
               for d in (0, 50, 200, 600)]
    assert valores == sorted(valores)


# --------------------------------------------------------------------------- #
# Los dos detalles que rompen una implementacion ingenua
# --------------------------------------------------------------------------- #
def test_un_bin_vacio_no_produce_infinito():
    """Sin epsilon, ln(0) manda el indice entero a infinito por un solo bin.

    Es el fallo clasico de esta formula: un indice infinito no dice "deriva
    enorme", dice "la implementacion esta rota".
    """
    ref = [float(i) for i in range(100)]
    act = [float(i) for i in range(50)]      # la mitad superior queda vacia
    r = deriva.psi(ref, act, n_bins=10)
    assert math_finito(r.valor)
    assert r.bins_vacios > 0
    assert "epsilon" in str(r)               # y lo declara en la salida


def math_finito(x: float) -> bool:
    return x == x and abs(x) != float("inf")


def test_los_bins_son_por_cuantiles_y_no_de_ancho_igual():
    """Con una cola larga a la derecha -- como los precios -- los bins de ancho
    igual meten casi todo en el primero y el indice deja de ver nada.

    Con cuantiles, una referencia sesgada reparte la masa: ningun bin se queda
    con mas de la mitad de los datos.
    """
    ref = [1.0] * 400 + [2.0] * 200 + [50.0] * 20 + [900.0] * 5
    cortes = deriva._cuantiles(ref, 10)
    fr = deriva._fracciones(ref, cortes)
    assert max(fr) < 0.75, f"un bin acapara la masa: {fr}"


def test_valores_repetidos_no_generan_bins_de_ancho_cero():
    """Un producto que pasa meses al mismo precio produce cortes iguales."""
    ref = [3.0] * 500
    r = deriva.psi(ref, [3.0] * 500, n_bins=10)
    assert math_finito(r.valor)


def test_el_indice_no_es_simetrico_y_eso_es_correcto():
    """La referencia define los bins, asi que el orden importa.

    La pregunta es "cuanto se movio lo actual respecto a lo que el modelo vio",
    no "cuanto difieren entre si".
    """
    a = [float(i) for i in range(1000)]
    b = [float(i) ** 1.5 for i in range(1000)]
    assert deriva.psi(a, b).valor != pytest.approx(deriva.psi(b, a).valor, abs=1e-6)


def test_sin_datos_no_se_inventa_un_numero():
    r = deriva.psi([], [1.0, 2.0])
    assert r.clase == "sin datos"
    assert r.valor == 0.0


# --------------------------------------------------------------------------- #
# CSI: por caracteristica, nunca promediado
# --------------------------------------------------------------------------- #
def test_el_csi_no_promedia_las_caracteristicas():
    """Promediar esconde el caso peligroso: una variable muy movida compensada
    por otras quietas. El modelo aguanta por compensacion, y eso es fragil."""
    quieta = [float(i % 10) for i in range(500)]
    movida_ref = [float(i % 10) for i in range(500)]
    movida_act = [float(i % 10) + 3 for i in range(500)]
    r = deriva.csi({"a": quieta, "b": movida_ref},
                   {"a": list(quieta), "b": movida_act})
    assert r["a"].clase == "estable"
    assert r["b"].clase == "significativa"
    nombre, peor = deriva.peor(r)
    assert nombre == "b"


def test_una_caracteristica_ausente_en_una_ventana_se_omite():
    r = deriva.csi({"a": [1.0, 2.0], "b": [1.0]}, {"a": [1.0, 2.0]})
    assert set(r) == {"a"}


def test_los_umbrales_son_los_declarados():
    assert deriva.clasificar(0.099) == "estable"
    assert deriva.clasificar(0.10) == "moderada"
    assert deriva.clasificar(0.249) == "moderada"
    assert deriva.clasificar(0.25) == "significativa"


# --------------------------------------------------------------------------- #
# Comparaciones degeneradas
# --------------------------------------------------------------------------- #
def test_dos_ventanas_que_no_se_solapan_no_son_deriva():
    """El artefacto que la primera corrida real destapo.

    CSI(mes) salio 6,10 "significativa" porque la ventana de referencia era
    julio-diciembre y la actual marzo-agosto: la distribucion del mes difiere por
    DEFINICION de las ventanas, no porque el mundo haya cambiado. Un indice alto
    ahi no es una senal, es una division entre casi-ceros.
    """
    ref = [float(m) for m in range(7, 13) for _ in range(200)]
    act = [float(m) for m in range(3, 9) for _ in range(200)]
    r = deriva.psi(ref, act, n_bins=10)
    assert r.valor > deriva.UMBRAL_SIGNIFICATIVA   # el numero SI es alto
    assert r.clase == "degenerada"                 # pero no se llama deriva
    assert "apenas se solapan" in str(r)


def test_una_degenerada_no_tapa_a_la_que_de_verdad_se_movio():
    """`peor()` las ignora: si no, la degenerada gana siempre por valor y
    esconde la unica que interesaba."""
    quieta = [float(i % 10) for i in range(500)]
    movida = [float(i % 10) + 3 for i in range(500)]
    degenerada_ref = [float(m) for m in range(7, 13) for _ in range(100)]
    degenerada_act = [float(m) for m in range(1, 7) for _ in range(100)]
    r = deriva.csi({"quieta": quieta, "movida": quieta, "artefacto": degenerada_ref},
                   {"quieta": list(quieta), "movida": movida,
                    "artefacto": degenerada_act})
    assert r["artefacto"].clase == "degenerada"
    assert r["artefacto"].valor > r["movida"].valor   # la degenerada puntua mas
    nombre, _ = deriva.peor(r)
    assert nombre == "movida"                          # y aun asi no gana


def test_si_todo_es_degenerado_no_se_elige_una_al_azar():
    ref = [float(m) for m in range(7, 13) for _ in range(100)]
    act = [float(m) for m in range(1, 7) for _ in range(100)]
    assert deriva.peor(deriva.csi({"a": ref}, {"a": act})) is None
