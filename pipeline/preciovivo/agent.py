"""Agente de mercado de Precio Vivo, orquestado con LangGraph.

POR QUÉ UN GRAFO EXPLÍCITO Y NO `create_react_agent`
----------------------------------------------------
LangGraph trae un ReAct prearmado en una línea. Acá se construye el `StateGraph`
a mano, y no por gusto: el prearmado esconde exactamente lo que hay que poder
mostrar y ajustar.

  * **El presupuesto de iteraciones.** Un agente que se queda en bucle
    llamando herramientas es el modo de falla más caro que existe: no se cae,
    factura. Acá el corte es un contador en el estado y un borde condicional.
  * **La política de reintento.** No todos los fallos merecen reintento. Un
    producto inexistente no se arregla repitiendo la llamada —se arregla
    diciéndole al modelo que busque el nombre—, mientras que un timeout de red
    sí. Esa distinción vive en `_clasificar_fallo`.
  * **La respuesta cuando se agota el presupuesto.** El prearmado devuelve el
    último mensaje, que puede ser una llamada a herramienta a medio hacer. Acá
    hay un nodo `rendirse` que responde con lo que sí se averiguó y dice qué
    faltó, en vez de dejar al usuario con un turno vacío.

EL GRAFO
--------
        ┌──────────┐
        │ razonar  │◄──────────────┐
        └────┬─────┘               │
             │ ¿pidió herramientas?│
       sí ───┴─── no               │
        │         └──► END         │
        ▼                          │
   ┌──────────┐   ok / error       │
   │ ejecutar ├────────────────────┘
   └────┬─────┘
        │ presupuesto agotado
        ▼
   ┌──────────┐
   │ rendirse ├──► END
   └──────────┘

Las herramientas son las mismas funciones de `service.py` que usan la API REST y
el servidor MCP. Una sola definición de la lógica de negocio, tres transportes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import service
from .service import ErrorDeConsulta

# Tope de vueltas razonar->ejecutar. Cinco alcanza para encadenar buscar ->
# comparar -> serie; más que eso, en la práctica, es un bucle.
MAX_ITERACIONES = int(os.environ.get("PRECIOVIVO_AGENTE_MAX_ITER", "5"))
# Reintentos por herramienta ante fallos TRANSITORIOS (ver `_clasificar_fallo`).
MAX_REINTENTOS = 2

AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.deepseek.com")
AI_MODEL = os.environ.get("AI_MODEL", "deepseek-chat")

SISTEMA = """\
Eres el analista de Precio Vivo, sobre precios mayoristas del Gran Mercado \
Mayorista de Lima (GMML), Perú. Respondes en español peruano, claro y sobrio, \
sin emojis.

Tienes herramientas para consultar los datos. Úsalas: NUNCA respondas un precio, \
una fecha o una variación de memoria.

REGLAS QUE NO SE NEGOCIAN:
1. Solo citas cifras que una herramienta te devolvió en esta conversación.
2. Los pronósticos son ESTIMACIONES de un modelo, no precios futuros. Dilo cada \
   vez que cites uno.
3. Las anomalías son desvíos estadísticos. Los datos dicen QUÉ pasó, no POR QUÉ. \
   No inventes causas (clima, paros, cosechas) que el dato no respalde.
4. Si una herramienta dice que un producto es ambiguo o no existe, usa \
   `buscar_productos` para corregirte; no adivines el nombre.
5. Si los datos no alcanzan para responder, dilo. Es una respuesta válida.
6. Cierra citando la fuente: MIDAGRI-GMML, cifras referenciales.
"""


# --------------------------------------------------------------------------- #
# Herramientas — envoltorios delgados sobre service.py
# --------------------------------------------------------------------------- #
def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


@tool
def precio_actual(producto: str) -> str:
    """Precio mayorista vigente de un producto, con su variación y volumen.

    Args:
        producto: nombre o slug, p. ej. 'papa blanca'.
    """
    return _json(service.precio_actual(producto))


@tool
def serie_historica(producto: str, desde: str | None = None,
                    hasta: str | None = None) -> str:
    """Evolución del precio entre dos fechas, con mínimo, máximo y promedio.

    Args:
        producto: nombre o slug.
        desde: fecha inicial ISO YYYY-MM-DD (opcional).
        hasta: fecha final ISO YYYY-MM-DD (opcional).
    """
    d = service.serie_historica(producto, desde, hasta)
    # Al modelo le sirve el resumen, no 519 puntos: mismo criterio que en MCP.
    d["puntos"] = d["puntos"][-20:]
    d["nota_puntos"] = "Se muestran los últimos 20 puntos; el resumen cubre todo el rango."
    return _json(d)


@tool
def pronostico(producto: str) -> str:
    """Pronóstico del próximo día hábil. ES UNA ESTIMACIÓN, no una cifra oficial.

    Args:
        producto: nombre o slug.
    """
    return _json(service.pronostico(producto))


@tool
def anomalias(producto: str | None = None, limite: int = 10) -> str:
    """Productos que se salieron de su comportamiento normal en el último día.

    Args:
        producto: filtra por producto (opcional).
        limite: máximo de anomalías a devolver.
    """
    return _json(service.anomalias(producto, limite))


@tool
def comparar_productos(productos: list[str], dias: int = 30) -> str:
    """Compara varios productos sobre la misma ventana temporal. Máximo 10.

    Args:
        productos: lista de nombres o slugs.
        dias: ventana hacia atrás.
    """
    return _json(service.comparar(productos, dias))


@tool
def buscar_productos(texto: str | None = None) -> str:
    """Busca en el catálogo por nombre parcial y devuelve los slugs válidos.

    Args:
        texto: texto parcial; omitir para el catálogo completo.
    """
    return _json({"productos": service.listar_productos(texto)})


@tool
def cobertura() -> str:
    """Qué cubre el dataset: mercado, rango de fechas y cantidad de productos."""
    return _json(service.meta())


HERRAMIENTAS = [precio_actual, serie_historica, pronostico, anomalias,
                comparar_productos, buscar_productos, cobertura]
POR_NOMBRE = {h.name: h for h in HERRAMIENTAS}


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #
class Estado(TypedDict):
    """Estado del grafo.

    `iteraciones` y `fallos` viven en el estado, no en variables sueltas, para
    que el corte por presupuesto sea una decisión del grafo —inspeccionable y
    testeable— y no un efecto secundario escondido en un nodo.
    """
    messages: Annotated[list[AnyMessage], add_messages]
    iteraciones: int
    fallos: list[str]


@dataclass
class Traza:
    """Lo que el agente hizo, para poder auditarlo.

    Un agente que solo devuelve texto es imposible de depurar: no se sabe si
    consultó, qué consultó ni si algo falló en el camino.
    """
    respuesta: str = ""
    herramientas_usadas: list[dict] = field(default_factory=list)
    iteraciones: int = 0
    fallos: list[str] = field(default_factory=list)
    completado: bool = True


# --------------------------------------------------------------------------- #
# Clasificación de fallos — la política de reintento, explícita
# --------------------------------------------------------------------------- #
def _clasificar_fallo(e: Exception) -> Literal["permanente", "transitorio"]:
    """Decide si repetir la llamada puede cambiar el resultado.

    `ErrorDeConsulta` es permanente por definición: el producto no existe o el
    nombre es ambiguo, y llamar otra vez con los mismos argumentos devolverá
    exactamente lo mismo. Lo que corresponde es devolverle el mensaje al modelo
    —que trae sugerencias— para que se corrija.

    Todo lo demás (red, disco, el snapshot regenerándose a mitad de lectura) se
    trata como transitorio y sí se reintenta.
    """
    if isinstance(e, (ErrorDeConsulta, ValueError, KeyError, TypeError)):
        return "permanente"
    return "transitorio"


def _ejecutar_una(nombre: str, args: dict) -> tuple[str, str | None]:
    """Ejecuta una herramienta con reintentos. Devuelve (contenido, fallo)."""
    herramienta = POR_NOMBRE.get(nombre)
    if herramienta is None:
        return (_json({"error": f"No existe la herramienta '{nombre}'.",
                       "disponibles": sorted(POR_NOMBRE)}),
                f"herramienta desconocida: {nombre}")

    ultimo: Exception | None = None
    for intento in range(MAX_REINTENTOS + 1):
        try:
            return herramienta.invoke(args), None
        except Exception as e:  # noqa: BLE001 - se clasifica justo abajo
            ultimo = e
            if _clasificar_fallo(e) == "permanente":
                # No se reintenta: el error ES la respuesta útil. El modelo lee
                # la sugerencia y corrige en la siguiente vuelta.
                return (_json({"error": str(e)}), f"{nombre}: {e}")
            if intento < MAX_REINTENTOS:
                continue
    return (_json({"error": f"La herramienta falló tras {MAX_REINTENTOS + 1} "
                            f"intentos: {ultimo}"}),
            f"{nombre}: {ultimo}")


# --------------------------------------------------------------------------- #
# Nodos
# --------------------------------------------------------------------------- #
def _modelo(temperatura: float = 0.0):
    from langchain_openai import ChatOpenAI

    clave = os.environ.get("AI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not clave:
        raise RuntimeError(
            "El agente necesita AI_API_KEY (proveedor OpenAI-compatible, DeepSeek "
            "por defecto). Sin clave usa la CLI (`precio pregunta`), que degrada "
            "a respuestas deterministas.")
    return ChatOpenAI(model=AI_MODEL, base_url=AI_BASE_URL, api_key=clave,
                      temperature=temperatura, timeout=60, max_retries=2)


def nodo_razonar(estado: Estado) -> dict:
    """Decide: ¿pedir más datos o responder ya?"""
    modelo = _modelo().bind_tools(HERRAMIENTAS)
    respuesta = modelo.invoke(estado["messages"])
    return {"messages": [respuesta], "iteraciones": estado["iteraciones"] + 1}


def nodo_ejecutar(estado: Estado) -> dict:
    """Corre las herramientas que pidió el modelo.

    No usa `ToolNode` a propósito: se necesita clasificar cada fallo y acumularlo
    en el estado. Un error de herramienta vuelve como `ToolMessage` normal —para
    el modelo es información, no una excepción— y además queda registrado en
    `fallos` para la traza.
    """
    ultimo = estado["messages"][-1]
    llamadas = getattr(ultimo, "tool_calls", None) or []
    mensajes, fallos = [], []
    for llamada in llamadas:
        contenido, fallo = _ejecutar_una(llamada["name"], llamada.get("args") or {})
        if fallo:
            fallos.append(fallo)
        mensajes.append(ToolMessage(content=contenido,
                                    tool_call_id=llamada["id"],
                                    name=llamada["name"]))
    return {"messages": mensajes, "fallos": estado["fallos"] + fallos}


def nodo_rendirse(estado: Estado) -> dict:
    """Se agotó el presupuesto: responder con lo que se tiene, no con nada.

    Se pide una síntesis SIN herramientas para que el modelo no pueda pedir otra
    vuelta. Si eso también falla, se devuelve un mensaje honesto en vez de una
    excepción: el usuario preguntó algo y merece saber qué pasó.
    """
    aviso = HumanMessage(content=(
        f"Alcanzaste el límite de {MAX_ITERACIONES} consultas a herramientas. "
        f"Responde AHORA con los datos que ya obtuviste, sin pedir más. Si no "
        f"alcanzan para responder del todo, dilo explícitamente y explica qué "
        f"faltó averiguar."))
    motivo = ""
    try:
        respuesta = _modelo().invoke(estado["messages"] + [aviso])
        texto = respuesta.content if isinstance(respuesta.content, str) else ""
    except Exception as e:  # noqa: BLE001 - el turno no puede quedar vacío
        texto, motivo = "", f" y la síntesis final falló ({e})"

    # Guard sobre el guard: aunque el modelo responda, puede devolver contenido
    # vacío (o solo llamadas a herramientas, que acá ya no van a ninguna parte).
    # Este nodo existe para que el turno NUNCA quede vacío, así que no puede
    # confiar en que el modelo colabore.
    if not (texto or "").strip():
        datos = sum(1 for m in estado["messages"] if getattr(m, "type", "") == "tool")
        respuesta = AIMessage(content=(
            f"No pude completar la consulta: agoté el presupuesto de "
            f"{MAX_ITERACIONES} llamadas a herramientas{motivo}. "
            f"Alcancé a reunir {datos} resultado(s) parcial(es). "
            f"Reformula la pregunta de forma más acotada — por ejemplo, "
            f"preguntando por un producto a la vez."))
    return {"messages": [respuesta]}


# --------------------------------------------------------------------------- #
# Bordes
# --------------------------------------------------------------------------- #
def decidir(estado: Estado) -> Literal["ejecutar", "rendirse", "__end__"]:
    """El borde condicional que hace del presupuesto una decisión del grafo."""
    ultimo = estado["messages"][-1]
    quiere_herramientas = bool(getattr(ultimo, "tool_calls", None))
    if not quiere_herramientas:
        return END
    if estado["iteraciones"] >= MAX_ITERACIONES:
        return "rendirse"
    return "ejecutar"


def construir_grafo():
    """Arma y compila el grafo. Separado de `responder` para poder inspeccionarlo
    y dibujarlo en los tests y en la documentación."""
    g = StateGraph(Estado)
    g.add_node("razonar", nodo_razonar)
    g.add_node("ejecutar", nodo_ejecutar)
    g.add_node("rendirse", nodo_rendirse)
    g.add_edge(START, "razonar")
    g.add_conditional_edges("razonar", decidir,
                            {"ejecutar": "ejecutar", "rendirse": "rendirse", END: END})
    g.add_edge("ejecutar", "razonar")
    g.add_edge("rendirse", END)
    return g.compile()


_grafo = None


def grafo():
    global _grafo
    if _grafo is None:
        _grafo = construir_grafo()
    return _grafo


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def responder(pregunta: str) -> Traza:
    """Responde una pregunta de mercado eligiendo herramientas. Devuelve la traza.

    El límite de recursión del grafo se fija por encima del presupuesto propio a
    propósito: quien debe cortar es `decidir` —que lleva a `rendirse` y produce
    una respuesta— y no la excepción de LangGraph, que dejaría el turno vacío.
    """
    estado_final = grafo().invoke(
        {"messages": [SystemMessage(content=SISTEMA), HumanMessage(content=pregunta)],
         "iteraciones": 0, "fallos": []},
        config={"recursion_limit": MAX_ITERACIONES * 2 + 10},
    )

    usadas = [
        {"herramienta": llamada["name"], "args": llamada.get("args")}
        for m in estado_final["messages"]
        for llamada in (getattr(m, "tool_calls", None) or [])
    ]

    final = estado_final["messages"][-1]
    return Traza(
        respuesta=final.content if isinstance(final.content, str) else str(final.content),
        herramientas_usadas=usadas,
        iteraciones=estado_final["iteraciones"],
        fallos=estado_final["fallos"],
        completado=estado_final["iteraciones"] < MAX_ITERACIONES,
    )


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print('Uso: python -m preciovivo.agent "¿por qué subió la papa esta semana?"')
        raise SystemExit(2)
    t = responder(" ".join(sys.argv[1:]))
    print(t.respuesta)
    print()
    print(f"[{t.iteraciones} iteracion(es) · herramientas: "
          f"{', '.join(u['herramienta'] for u in t.herramientas_usadas) or 'ninguna'}]")
    if t.fallos:
        print(f"[fallos: {'; '.join(t.fallos)}]")
    if not t.completado:
        print("[presupuesto agotado: la respuesta puede estar incompleta]")


if __name__ == "__main__":
    main()
