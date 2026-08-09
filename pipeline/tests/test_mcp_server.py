"""Pruebas del servidor MCP (preciovivo.mcp_server).

Lo que se verifica es lo que distingue una herramienta MCP de un endpoint REST:
que las salidas quepan en una ventana de contexto, que se expliquen solas, y que
los errores le enseñen al modelo cómo corregirse. La lógica de negocio no se
reprueba acá — vive en `service.py`.
"""
from __future__ import annotations

import asyncio
import json

from preciovivo import mcp_server as M


def _j(texto: str) -> dict:
    return json.loads(texto)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def test_expone_las_herramientas_esperadas(snapshot_en_disco):
    nombres = {t.name for t in asyncio.run(M.mcp.list_tools())}
    assert nombres == {
        "precio_actual", "serie_historica", "pronostico", "anomalias",
        "comparar_productos", "buscar_productos", "consultar", "cobertura",
    }


def test_todas_son_de_solo_lectura(snapshot_en_disco):
    """Ninguna herramienta escribe. Un cliente MCP debe poder saberlo sin
    leer el código."""
    for t in asyncio.run(M.mcp.list_tools()):
        assert t.annotations is not None, t.name
        assert t.annotations.read_only_hint is True, t.name


def test_las_descripciones_dicen_cuando_usarlas(snapshot_en_disco):
    """Es lo único que el modelo lee para elegir herramienta.

    No basta con describir QUÉ hace: si dos herramientas se parecen, lo que
    desempata es cuándo conviene cada una. Por eso se exige la guía explícita.
    """
    for t in asyncio.run(M.mcp.list_tools()):
        assert t.description and len(t.description) > 60, t.name
        assert "úsala" in t.description.lower(), (
            f"la descripción de '{t.name}' no dice CUÁNDO usarla")


def test_expone_el_catalogo_como_recurso(snapshot_en_disco):
    uris = [str(r.uri) for r in asyncio.run(M.mcp.list_resources())]
    assert "preciovivo://catalogo" in uris


def test_las_instrucciones_fijan_las_reglas_de_cita(snapshot_en_disco):
    ins = M.INSTRUCCIONES
    assert "referenciales" in ins.lower()
    assert "no POR QUÉ" in ins or "no inventes causas" in ins.lower()


# --------------------------------------------------------------------------- #
# Salidas dimensionadas para un contexto
# --------------------------------------------------------------------------- #
def test_la_serie_se_muestrea_en_vez_de_volcarse(snapshot_en_disco, monkeypatch):
    """Un volcado de cientos de puntos llena la ventana y diluye la señal."""
    monkeypatch.setattr(M, "MAX_PUNTOS_SERIE", 10)
    d = _j(M.serie_historica("tomate"))
    assert len(d["puntos"]) == 10
    assert "muestreo" in d
    assert "API REST" in d["muestreo"]


def test_el_muestreo_conserva_los_extremos(snapshot_en_disco, monkeypatch,
                                           snapshot_sintetico):
    """Quedarse con los últimos N escondería el pico viejo que se busca; y el
    dato más reciente nunca puede perderse."""
    monkeypatch.setattr(M, "MAX_PUNTOS_SERIE", 5)
    d = _j(M.serie_historica("tomate"))
    assert d["puntos"][0]["fecha"] == snapshot_sintetico["fechas"][0]
    assert d["puntos"][-1]["fecha"] == snapshot_sintetico["fechas"][-1]


def test_serie_corta_no_se_muestrea(snapshot_en_disco, snapshot_sintetico):
    f = snapshot_sintetico["fechas"]
    d = _j(M.serie_historica("tomate", desde=f[0], hasta=f[3]))
    assert "muestreo" not in d
    assert len(d["puntos"]) == 4


# --------------------------------------------------------------------------- #
# Las salidas se explican solas
# --------------------------------------------------------------------------- #
def test_el_precio_lleva_unidades_y_disclaimer(snapshot_en_disco):
    d = _j(M.precio_actual("tomate"))
    assert "soles por kilogramo" in d["unidades"]
    assert "no oficial" in d["nota"].lower()


def test_el_pronostico_se_marca_como_estimacion(snapshot_en_disco):
    d = _j(M.pronostico("tomate"))
    assert d["disponible"] is False  # el fixture no trae forecast
    assert "motivo" in d


def test_las_anomalias_niegan_causalidad(snapshot_en_disco):
    d = _j(M.anomalias())
    assert "NO explicación causal" in d["metodo"]


def test_cobertura_dice_lo_que_NO_cubre(snapshot_en_disco):
    """Sirve para que el modelo sepa cuándo la pregunta cae fuera del dataset."""
    d = _j(M.cobertura())
    assert "minoristas" in d["no_cubre"]
    assert "otros países" in d["no_cubre"]


# --------------------------------------------------------------------------- #
# Los errores enseñan
# --------------------------------------------------------------------------- #
def test_producto_ambiguo_devuelve_las_opciones(snapshot_en_disco):
    """No es una excepción de transporte: es información que el modelo lee para
    corregirse en la vuelta siguiente."""
    d = _j(M.precio_actual("papa"))
    assert "error" in d
    assert "papa-blanca" in d["error"]
    assert "buscar_productos" in d["sugerencia"]


def test_producto_inexistente(snapshot_en_disco):
    d = _j(M.precio_actual("salmon"))
    assert "error" in d and "No existe" in d["error"]


def test_buscar_productos_da_los_slugs_validos(snapshot_en_disco):
    d = _j(M.buscar_productos("papa"))
    assert {p["slug"] for p in d["productos"]} == {"papa-blanca", "papa-amarilla", "papaya"}


def test_consultar_sin_indice_sugiere_alternativas(snapshot_en_disco):
    """Sin índice RAG, la herramienta no debe dejar al modelo sin salida."""
    d = _j(M.consultar("¿por qué subió el tomate?"))
    if "error" in d:
        assert "precio_actual" in d["sugerencia"]
    else:
        assert "instruccion" in d


def test_comparar_acota_los_limites(snapshot_en_disco):
    d = _j(M.comparar_productos(["tomate", "papaya"], dias=99999))
    assert d["ventana_dias"] == 730  # se recorta, no revienta


def test_anomalias_acota_el_limite(snapshot_en_disco):
    assert _j(M.anomalias(limite=9999))["n"] >= 0


# --------------------------------------------------------------------------- #
# Llamada por el protocolo
# --------------------------------------------------------------------------- #
def test_call_tool_por_el_protocolo(snapshot_en_disco):
    res = asyncio.run(M.mcp.call_tool("precio_actual", {"producto": "tomate"}))
    texto = res.content[0].text
    assert _j(texto)["slug"] == "tomate"


def test_json_compacto(snapshot_en_disco):
    """Cada espacio de separador es un token que se paga.

    Se compara contra el formato por defecto de json en vez de buscar ', ' en el
    texto: las comas con espacio también aparecen DENTRO de los valores (el
    disclaimer las tiene) y buscarlas a ciegas mediría la prosa, no el formato.
    """
    import json as _json

    salida = M.precio_actual("tomate")
    datos = _json.loads(salida)
    assert len(salida) < len(_json.dumps(datos, ensure_ascii=False))
