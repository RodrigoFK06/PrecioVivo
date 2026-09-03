"""Pruebas de la doble comprobación de cifras.

La prueba que importa es `test_caza_una_cifra_inventada`: un detector que nunca
marca nada es indistinguible de uno roto, y la única forma de separarlos es
enseñarle algo que DEBE marcar. Es el mismo error que ya se cometió una vez en
este repositorio —la regla descartaba los valores menores que 10 y por tanto
TODOS los precios, reportando cero invenciones sobre 203 cifras— así que aquí se
prueba contra invenciones conocidas, no leyendo el resultado.
"""
from __future__ import annotations

from preciovivo.verificador import (
    afirma_un_precio,
    clasificar_cifras,
    instruccion_de_correccion,
    numeros,
    verificar,
)

CONTEXTO = (
    "Papa Blanca — semana 2025-W08 (del 17 al 21 de febrero).\n"
    "Precio: abrió en S/ 1.20/kg el 17 de febrero y cerró en S/ 1.50/kg "
    "el 21 de febrero (25.0% en el periodo).\n"
    "Ingreso: 2 355 t en el periodo."
)


# --------------------------------------------------------------------------- #
# Lo que tiene que cazar
# --------------------------------------------------------------------------- #
def test_caza_una_cifra_inventada():
    v = verificar("La papa cerró en S/ 9.99 por kilo.", CONTEXTO)
    assert not v.ok
    assert 9.99 in v.sin_respaldo


def test_un_precio_inventado_con_decimales_no_se_escapa():
    """El hueco histórico: la regla vieja tiraba todo lo menor que 10, que son
    justamente todos los precios de esta serie."""
    v = verificar("Está a S/ 4.87 el kilo.", CONTEXTO)
    assert not v.ok
    assert 4.87 in v.sin_respaldo


# --------------------------------------------------------------------------- #
# Lo que NO puede marcar
# --------------------------------------------------------------------------- #
def test_una_cifra_del_contexto_pasa():
    assert verificar("Cerró en S/ 1.50/kg.", CONTEXTO).ok


def test_una_resta_derivada_pasa():
    """S/ 1.50 - S/ 1.20 = S/ 0.30. Es aritmética legítima sobre el dato y
    castigarla haría que el arnés penalizara justo lo que se quiere."""
    c = clasificar_cifras("Subió 0.30 soles en la semana.", CONTEXTO)
    assert 0.30 in c["derivadas"]
    assert not c["sin_respaldo"]


def test_una_variacion_porcentual_derivada_pasa():
    c = clasificar_cifras("Un alza de 25.0% en el periodo.", CONTEXTO)
    assert not c["sin_respaldo"]


def test_los_anios_no_cuentan_como_cifras():
    assert 2025.0 not in numeros("en 2025 la papa subió")


def test_los_enteros_de_un_digito_no_cuentan():
    """Suelen ser conteos del propio texto ("2 días", "los 5 más baratos"), no
    datos del reporte."""
    assert numeros("los 5 productos más baratos en 2 días") == set()


def test_el_separador_de_millares_no_parte_el_numero():
    assert 2355.0 in numeros("Ingreso: 2 355 t")


def test_la_coma_decimal_y_el_punto_son_lo_mismo():
    assert numeros("S/ 1,50") == numeros("S/ 1.50")


# --------------------------------------------------------------------------- #
# Abstención
# --------------------------------------------------------------------------- #
def test_reconoce_que_una_respuesta_afirma_un_precio():
    assert afirma_un_precio("Está a S/ 4.20 el kilo.")
    assert afirma_un_precio("Cuesta 4.20 soles.")


def test_una_abstencion_correcta_no_afirma_precio():
    assert not afirma_un_precio("No seguimos ese producto en el GMML.")


# --------------------------------------------------------------------------- #
# La corrección
# --------------------------------------------------------------------------- #
def test_la_correccion_nombra_las_cifras_exactas():
    """Repetir «no inventes cifras» no sirve: ya estaba en el prompt del sistema
    y no bastó. Lo accionable es el número concreto."""
    v = verificar("Cerró en S/ 9.99 tras tocar S/ 12.34.", CONTEXTO)
    texto = instruccion_de_correccion(v)
    assert "9.99" in texto
    assert "12.34" in texto


def test_un_veredicto_bueno_no_tiene_motivo():
    assert verificar("Cerró en S/ 1.50/kg.", CONTEXTO).motivo is None
