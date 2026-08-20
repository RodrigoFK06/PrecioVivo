"""Pruebas del DETECTOR de invención de cifras (`evals/run_generacion.py`).

POR QUÉ ESTE ARCHIVO EXISTE
---------------------------
La primera versión del detector reportó «203 cifras afirmadas, 0 invenciones» y
el número era falso: descartaba todo valor menor que 10 para evitar ruido, y con
ello descartaba TODOS LOS PRECIOS. Solo había mirado toneladas y fechas.

Un indicador que siempre sale perfecto es indistinguible de uno roto. La única
forma de separarlos es enseñarle algo que DEBE marcar, y eso es lo que hace este
archivo: el detector se prueba contra invenciones conocidas, no contra su propia
salida.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.run_generacion import (  # noqa: E402
    afirma_un_precio,
    clasificar_cifras,
)

CONTEXTO = (
    "Papa Blanca: 2026-08-18 precio S/ 1.03 por kg, ayer S/ 1.00. "
    "Ingreso 1 862 t, promedio 7 dias 1 553 t."
)


def _categoria(respuesta: str) -> list[str]:
    return [k for k, v in clasificar_cifras(respuesta, CONTEXTO).items() if v]


@pytest.mark.parametrize("respuesta,esperado", [
    # El caso que la primera versión no veía: un precio, que es < 10.
    ("La papa blanca cerró en S/ 1.03 por kilo.", "en_contexto"),
    ("Ingresaron 1 862 toneladas.", "en_contexto"),
    # Aritmética legítima sobre dos cifras del contexto.
    ("Subió 3.0% respecto a ayer.", "derivadas"),
    ("El ingreso creció en 309 toneladas.", "derivadas"),
    # Invención pura.
    ("La papa blanca cerró en S/ 4.87 por kilo.", "sin_respaldo"),
    ("Ingresaron 9 410 toneladas.", "sin_respaldo"),
    ("Subió 47.5% respecto a ayer.", "sin_respaldo"),
])
def test_clasifica_cada_tipo_de_cifra(respuesta: str, esperado: str):
    assert _categoria(respuesta) == [esperado]


def test_un_precio_inventado_no_se_escapa_por_ser_pequeno():
    """La regresión concreta, con su nombre.

    S/ 4.87 es menor que 10. La versión que filtraba `v < 10` lo dejaba pasar y
    reportaba cero invenciones sobre una respuesta que se había inventado el
    precio — el único dato que este producto promete no inventar.
    """
    c = clasificar_cifras("El kilo está en S/ 4.87.", CONTEXTO)
    assert c["sin_respaldo"] == [4.87]


def test_los_conteos_del_propio_texto_no_son_invencion():
    """"los 5 más baratos" no es un dato del mercado, es cómo se redactó.

    Marcarlos daría falsos positivos constantes, y un detector que cría lobos
    deja de mirarse.
    """
    assert _categoria("Te muestro los 5 más baratos de hoy.") == []


def test_los_anios_no_cuentan_como_cifra():
    assert _categoria("En 2026 el mercado abrió con normalidad.") == []


def test_acepta_las_tres_formas_de_escribir_un_numero():
    """`1.03`, `1,03` y `1 862` con espacio de millares conviven en este dominio:
    el contexto los escribe de una forma y el modelo puede responder en otra."""
    assert _categoria("El precio fue de 1,03 soles.") == ["en_contexto"]
    assert _categoria("Ingresaron 1862 toneladas.") == ["en_contexto"]


def test_deteccion_de_precio_para_los_casos_de_abstencion():
    assert not afirma_un_precio("La papaya no está en el catálogo del GMML.")
    assert afirma_un_precio("La papaya cuesta S/ 1.03 por kilo.")
    assert afirma_un_precio("Está a 1.03 soles el kilo.")
