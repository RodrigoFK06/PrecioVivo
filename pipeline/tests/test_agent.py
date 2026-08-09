"""Pruebas del agente LangGraph (preciovivo.agent).

Sin red ni clave: se prueban las piezas que NO dependen del modelo, que son
justamente las que se construyeron a mano en vez de usar `create_react_agent`:

  * la política de reintento (`_clasificar_fallo`),
  * la ejecución de herramientas con sus fallos,
  * el borde de decisión que hace cumplir el presupuesto,
  * la forma del grafo.

El nodo `razonar` necesita un LLM y por eso se prueba con un modelo falso: lo que
importa es que el GRAFO se comporte, no que el modelo acierte.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from preciovivo import agent as A
from preciovivo.service import ErrorDeConsulta


def _llamada(nombre: str, args: dict, id_: str = "1") -> dict:
    return {"name": nombre, "args": args, "id": id_, "type": "tool_call"}


# --------------------------------------------------------------------------- #
# Forma del grafo
# --------------------------------------------------------------------------- #
def test_el_grafo_tiene_los_nodos_explicitos():
    """Los tres nodos son la razón de no usar el ReAct prearmado."""
    nodos = set(A.construir_grafo().get_graph().nodes)
    assert {"razonar", "ejecutar", "rendirse"} <= nodos


def test_las_herramientas_estan_registradas():
    assert {h.name for h in A.HERRAMIENTAS} == {
        "precio_actual", "serie_historica", "pronostico", "anomalias",
        "comparar_productos", "buscar_productos", "cobertura"}


def test_el_sistema_prohibe_inventar_causas():
    assert "no POR QUÉ" in A.SISTEMA or "No inventes causas" in A.SISTEMA
    assert "ESTIMACIONES" in A.SISTEMA


# --------------------------------------------------------------------------- #
# Política de reintento
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("error,esperado", [
    (ErrorDeConsulta("no existe"), "permanente"),
    (ValueError("mal"), "permanente"),
    (KeyError("falta"), "permanente"),
    (ConnectionError("sin red"), "transitorio"),
    (TimeoutError("lento"), "transitorio"),
    (OSError("disco"), "transitorio"),
])
def test_clasificacion_de_fallos(error, esperado):
    assert A._clasificar_fallo(error) == esperado


def test_un_fallo_permanente_no_se_reintenta(monkeypatch):
    """Repetir la llamada devolvería lo mismo. El error ES la respuesta útil."""
    llamadas = {"n": 0}

    def falla(args):
        llamadas["n"] += 1
        raise ErrorDeConsulta("'papa' es ambiguo")

    monkeypatch.setitem(A.POR_NOMBRE, "falsa", type("T", (), {"invoke": staticmethod(falla)})())
    contenido, fallo = A._ejecutar_una("falsa", {})
    assert llamadas["n"] == 1
    assert "ambiguo" in json.loads(contenido)["error"]
    assert fallo


def test_un_fallo_transitorio_se_reintenta(monkeypatch):
    intentos = {"n": 0}

    def a_veces(args):
        intentos["n"] += 1
        if intentos["n"] <= 2:
            raise ConnectionError("sin red")
        return '{"ok":true}'

    monkeypatch.setitem(A.POR_NOMBRE, "falsa", type("T", (), {"invoke": staticmethod(a_veces)})())
    contenido, fallo = A._ejecutar_una("falsa", {})
    assert intentos["n"] == 3
    assert fallo is None
    assert json.loads(contenido)["ok"] is True


def test_se_rinde_tras_agotar_los_reintentos(monkeypatch):
    def siempre_falla(args):
        raise ConnectionError("sin red")

    monkeypatch.setitem(A.POR_NOMBRE, "falsa",
                        type("T", (), {"invoke": staticmethod(siempre_falla)})())
    contenido, fallo = A._ejecutar_una("falsa", {})
    assert "intentos" in json.loads(contenido)["error"]
    assert fallo


def test_herramienta_desconocida_lista_las_validas():
    contenido, fallo = A._ejecutar_una("no_existe", {})
    d = json.loads(contenido)
    assert "precio_actual" in d["disponibles"]
    assert "desconocida" in fallo


# --------------------------------------------------------------------------- #
# Ejecución contra datos reales
# --------------------------------------------------------------------------- #
def test_las_herramientas_leen_el_dataset(snapshot_en_disco):
    contenido, fallo = A._ejecutar_una("precio_actual", {"producto": "tomate"})
    assert fallo is None
    assert json.loads(contenido)["slug"] == "tomate"


def test_la_serie_se_recorta_para_el_contexto(snapshot_en_disco):
    contenido, _ = A._ejecutar_una("serie_historica", {"producto": "tomate"})
    d = json.loads(contenido)
    assert len(d["puntos"]) <= 20
    assert "nota_puntos" in d


def test_el_nodo_ejecutar_acumula_fallos(snapshot_en_disco):
    mensaje = AIMessage(content="", tool_calls=[
        _llamada("precio_actual", {"producto": "tomate"}, "1"),
        _llamada("precio_actual", {"producto": "salmon"}, "2"),
    ])
    salida = A.nodo_ejecutar({"messages": [mensaje], "iteraciones": 1, "fallos": []})
    assert len(salida["messages"]) == 2
    assert len(salida["fallos"]) == 1  # solo el salmón falló
    # El error vuelve como ToolMessage normal: para el modelo es información.
    assert all(m.type == "tool" for m in salida["messages"])


# --------------------------------------------------------------------------- #
# El presupuesto es una decisión del grafo
# --------------------------------------------------------------------------- #
def _estado(mensaje, iteraciones):
    return {"messages": [mensaje], "iteraciones": iteraciones, "fallos": []}


def test_sin_llamadas_termina():
    assert A.decidir(_estado(AIMessage(content="listo"), 1)) == "__end__"


def test_con_llamadas_y_presupuesto_ejecuta():
    m = AIMessage(content="", tool_calls=[_llamada("cobertura", {})])
    assert A.decidir(_estado(m, 1)) == "ejecutar"


def test_al_agotarse_el_presupuesto_va_a_rendirse():
    """Un agente en bucle no se cae: factura. El corte es explícito."""
    m = AIMessage(content="", tool_calls=[_llamada("cobertura", {})])
    assert A.decidir(_estado(m, A.MAX_ITERACIONES)) == "rendirse"
    assert A.decidir(_estado(m, A.MAX_ITERACIONES + 3)) == "rendirse"


def test_rendirse_responde_aunque_falle_el_modelo(monkeypatch):
    """El prearmado dejaría el turno vacío; acá el usuario preguntó algo y merece
    saber qué pasó."""
    def sin_modelo(*a, **kw):
        raise RuntimeError("sin clave")

    monkeypatch.setattr(A, "_modelo", sin_modelo)
    salida = A.nodo_rendirse({"messages": [HumanMessage(content="hola")],
                              "iteraciones": 5, "fallos": []})
    texto = salida["messages"][0].content
    assert "presupuesto" in texto.lower() or "límite" in texto.lower()
    assert "Reformula" in texto


# --------------------------------------------------------------------------- #
# Recorrido completo con un modelo falso
# --------------------------------------------------------------------------- #
class _ModeloFalso:
    """Encadena respuestas prefijadas. Prueba el GRAFO, no la calidad del LLM."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.vistas = []

    def bind_tools(self, _herramientas):
        return self

    def invoke(self, mensajes):
        self.vistas.append(mensajes)
        return self.respuestas.pop(0) if self.respuestas else AIMessage(content="fin")


def test_recorrido_pide_herramienta_y_luego_responde(snapshot_en_disco, monkeypatch):
    falso = _ModeloFalso([
        AIMessage(content="", tool_calls=[_llamada("precio_actual", {"producto": "tomate"})]),
        AIMessage(content="El tomate está a S/ 3.00/kg. Fuente: MIDAGRI-GMML."),
    ])
    monkeypatch.setattr(A, "_modelo", lambda *a, **kw: falso)
    monkeypatch.setattr(A, "_grafo", None)

    t = A.responder("¿a cuánto está el tomate?")
    assert "3.00" in t.respuesta
    assert [u["herramienta"] for u in t.herramientas_usadas] == ["precio_actual"]
    assert t.iteraciones == 2
    assert t.completado is True
    assert t.fallos == []


def test_recorrido_se_corta_al_agotar_el_presupuesto(snapshot_en_disco, monkeypatch):
    """Un modelo que pide herramientas para siempre debe terminar igual.

    Cada vuelta usa un AIMessage NUEVO a propósito: el reducer `add_messages`
    deduplica por id, así que reutilizar el mismo objeto colapsaría las
    iteraciones y el test no probaría nada.
    """
    falso = _ModeloFalso(
        [AIMessage(content="", id=f"ia-{i}",
                   tool_calls=[_llamada("cobertura", {}, f"c{i}")])
         for i in range(A.MAX_ITERACIONES + 2)]
        + [AIMessage(content="Con lo que tengo: ...", id="final")])
    monkeypatch.setattr(A, "_modelo", lambda *a, **kw: falso)
    monkeypatch.setattr(A, "_grafo", None)

    t = A.responder("dame todo")
    assert t.completado is False
    assert t.iteraciones == A.MAX_ITERACIONES
    assert t.respuesta  # nunca un turno vacío


def test_la_traza_registra_los_fallos(snapshot_en_disco, monkeypatch):
    falso = _ModeloFalso([
        AIMessage(content="", tool_calls=[_llamada("precio_actual", {"producto": "salmon"})]),
        AIMessage(content="No encuentro ese producto en el GMML."),
    ])
    monkeypatch.setattr(A, "_modelo", lambda *a, **kw: falso)
    monkeypatch.setattr(A, "_grafo", None)

    t = A.responder("¿a cuánto está el salmón?")
    assert t.fallos and "salmon" in t.fallos[0]
    assert t.respuesta


def test_sin_clave_el_error_dice_la_alternativa(monkeypatch):
    for var in ("AI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="AI_API_KEY"):
        A._modelo()
