"""Servidor MCP de Precio Vivo: precios mayoristas peruanos para cualquier cliente MCP.

QUÉ EXPONE
----------
La serie diaria del Gran Mercado Mayorista de Lima —~73 productos desde julio de
2024, precios en S//kg, volumen en toneladas— como herramientas MCP. Hasta donde
sabemos no existe un servidor MCP de datos de mercados mayoristas peruanos.

DISEÑAR PARA UN LLM, NO PARA UN PROGRAMA
----------------------------------------
Es la diferencia con la API REST, y condiciona casi todo lo de abajo. Un endpoint
REST puede devolver 519 puntos y que el cliente pagine. Una herramienta MCP
devuelve al CONTEXTO de un modelo, donde ese volcado tiene tres costos: llena la
ventana, diluye la señal y se paga por token.

De ahí las decisiones:

* **Las series se resumen, no se vuelcan.** `serie_historica` devuelve
  estadísticos y una muestra acotada, no todos los puntos. Quien necesite el
  detalle completo tiene la API REST.
* **Cada salida se explica sola.** Un pronóstico lleva su advertencia y una
  anomalía dice que no explica causas, en la misma respuesta. El modelo que lee
  esto no vio nuestro README, y un número sin contexto se reenvía como si fuera
  oficial.
* **Los errores enseñan.** "No existe el producto 'papa'" es inútil; "coincide
  con 10 productos: papa-blanca, papa-amarilla…" deja que el modelo se corrija
  solo, sin otra vuelta con el usuario.
* **Las descripciones dicen CUÁNDO usar cada herramienta**, no solo qué hace: es
  lo único que el modelo lee para elegir.

TODO ES DE SOLO LECTURA. Ninguna herramienta escribe nada.
"""
from __future__ import annotations

import json
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import service
from .service import ErrorDeConsulta

# Cuántos puntos de serie se devuelven como máximo. Ver la nota de diseño: el
# destino es la ventana de contexto de un modelo, no un archivo.
MAX_PUNTOS_SERIE = 60

SOLO_LECTURA = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                               idempotentHint=True, openWorldHint=False)

INSTRUCCIONES = """\
Precio Vivo expone precios mayoristas del Gran Mercado Mayorista de Lima (GMML),
Perú, derivados de los reportes diarios de MIDAGRI/EMMSA.

Cómo elegir herramienta:
- Un precio de hoy -> `precio_actual`
- Cómo evolucionó algo -> `serie_historica`
- Qué se espera mañana -> `pronostico` (ES UNA ESTIMACIÓN, no un dato oficial)
- Qué se salió de lo normal -> `anomalias`
- Varios productos a la vez -> `comparar_productos`
- No sabes el nombre exacto -> `buscar_productos`
- Una pregunta abierta en español -> `consultar`

Reglas al citar estos datos:
1. Son cifras REFERENCIALES, no oficiales. Cita siempre la fuente (MIDAGRI-GMML).
2. Los pronósticos son estimaciones de un modelo. Nunca los presentes como el
   precio futuro.
3. Las anomalías son desvíos estadísticos. Los datos dicen QUÉ pasó, no POR QUÉ:
   no inventes causas (clima, paros, cosecha) que el dato no respalde.
4. Solo hay precios mayoristas de Lima. No hay precios minoristas ni de otras
   ciudades.
"""

mcp = MCPServer(
    name="preciovivo",
    title="Precio Vivo — precios mayoristas del Perú",
    description=("Serie diaria de precios y volúmenes del Gran Mercado Mayorista "
                 "de Lima (GMML), con pronósticos y detección de anomalías."),
    instructions=INSTRUCCIONES,
    website_url="https://precio-vivo.vercel.app",
)


def _ok(payload: dict) -> str:
    """Serializa una respuesta. JSON compacto: cada espacio es un token."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _error(e: Exception) -> str:
    """Un error de dominio es información útil para el modelo, no un fallo.

    Se devuelve como resultado (no como excepción de transporte) para que el
    modelo pueda leer la sugerencia y reintentar con el slug correcto.
    """
    return _ok({"error": str(e),
                "sugerencia": "Usa `buscar_productos` para ver nombres válidos."})


# --------------------------------------------------------------------------- #
# Herramientas
# --------------------------------------------------------------------------- #
@mcp.tool(
    description=("Precio mayorista vigente de un producto en el GMML, con su "
                 "variación contra el día anterior y el volumen que ingresó. "
                 "Úsala para '¿a cuánto está X?'. Acepta nombre o slug, con o "
                 "sin acentos."),
    annotations=SOLO_LECTURA,
)
def precio_actual(
    producto: Annotated[str, "Nombre o slug, p. ej. 'papa blanca' o 'tomate'."],
) -> str:
    try:
        d = service.precio_actual(producto)
    except ErrorDeConsulta as e:
        return _error(e)
    return _ok({
        **d,
        "unidades": "precio en soles por kilogramo; volumen en toneladas",
        "nota": "Cifra referencial derivada del reporte oficial, no oficial.",
    })


@mcp.tool(
    description=("Cómo evolucionó el precio de un producto entre dos fechas. "
                 "Devuelve estadísticos y una MUESTRA de puntos, no la serie "
                 "completa, para no llenar el contexto. Úsala para tendencias, "
                 "comparaciones temporales y '¿cuánto costaba en X?'."),
    annotations=SOLO_LECTURA,
)
def serie_historica(
    producto: Annotated[str, "Nombre o slug del producto."],
    desde: Annotated[str | None, "Fecha inicial ISO YYYY-MM-DD."] = None,
    hasta: Annotated[str | None, "Fecha final ISO YYYY-MM-DD."] = None,
) -> str:
    try:
        d = service.serie_historica(producto, desde, hasta)
    except ErrorDeConsulta as e:
        return _error(e)

    puntos = d.pop("puntos")
    # Muestreo uniforme: conserva la FORMA de la serie. Quedarse con los últimos
    # 60 escondería justamente el pico viejo que la pregunta suele buscar.
    if len(puntos) > MAX_PUNTOS_SERIE:
        paso = len(puntos) / MAX_PUNTOS_SERIE
        muestra = [puntos[int(i * paso)] for i in range(MAX_PUNTOS_SERIE)]
        if muestra[-1] != puntos[-1]:
            muestra[-1] = puntos[-1]  # el dato más reciente nunca se pierde
        d["muestreo"] = (f"{len(puntos)} puntos reducidos a {len(muestra)} por "
                         f"muestreo uniforme. Para la serie completa usa la API REST.")
        puntos = muestra
    d["puntos"] = puntos
    return _ok(d)


@mcp.tool(
    description=("Pronóstico de precio para el próximo día hábil. Úsala solo si "
                 "preguntan explícitamente por el futuro; para el precio de hoy "
                 "usa `precio_actual`. ATENCIÓN: es una ESTIMACIÓN de un modelo "
                 "estadístico, no una cifra publicada. Incluye el error del "
                 "modelo y si le gana a su baseline — si no le gana, dilo al "
                 "citarlo."),
    annotations=SOLO_LECTURA,
)
def pronostico(
    producto: Annotated[str, "Nombre o slug del producto."],
) -> str:
    try:
        return _ok(service.pronostico(producto))
    except ErrorDeConsulta as e:
        return _error(e)


@mcp.tool(
    description=("Productos cuyo precio o volumen se salió de su comportamiento "
                 "habitual en el último día, por z-score robusto. Señala QUÉ se "
                 "desvió, NO por qué: no atribuyas causas. Úsala para '¿pasó "
                 "algo raro?' o '¿hubo movimientos inusuales?'."),
    annotations=SOLO_LECTURA,
)
def anomalias(
    producto: Annotated[str | None, "Filtra por producto; omite para ver todas."] = None,
    limite: Annotated[int, "Máximo de anomalías (1-50)."] = 10,
) -> str:
    try:
        return _ok(service.anomalias(producto, max(1, min(limite, 50))))
    except ErrorDeConsulta as e:
        return _error(e)


@mcp.tool(
    description=("Compara varios productos sobre la misma ventana temporal: "
                 "precio actual, mínimo, máximo, promedio y variación. Úsala "
                 "para '¿qué conviene más?' o '¿cuál subió más?'. Máximo 10."),
    annotations=SOLO_LECTURA,
)
def comparar_productos(
    productos: Annotated[list[str], "Nombres o slugs. Máximo 10."],
    dias: Annotated[int, "Ventana en días hacia atrás (1-730)."] = 30,
) -> str:
    try:
        return _ok(service.comparar(productos, max(1, min(dias, 730))))
    except ErrorDeConsulta as e:
        return _error(e)


@mcp.tool(
    description=("Busca en el catálogo por nombre parcial y devuelve los slugs "
                 "válidos con su precio. Úsala PRIMERO si no conoces el nombre "
                 "exacto, o cuando otra herramienta responda que el producto es "
                 "ambiguo."),
    annotations=SOLO_LECTURA,
)
def buscar_productos(
    texto: Annotated[str | None, "Texto parcial; omite para el catálogo completo."] = None,
) -> str:
    items = service.listar_productos(texto)
    return _ok({"n": len(items), "productos": items})


@mcp.tool(
    description=("Pregunta abierta en español sobre el mercado. Recupera la "
                 "evidencia relevante —serie del periodo, anomalías, "
                 "estacionalidad, co-movimientos— y la devuelve como CONTEXTO "
                 "para que tú redactes la respuesta. Úsala para preguntas de "
                 "'por qué' o 'qué pasó', que las otras herramientas no cubren."),
    annotations=SOLO_LECTURA,
)
def consultar(
    pregunta: Annotated[str, "Pregunta en español, p. ej. '¿por qué subió la papa esta semana?'"],
    k: Annotated[int, "Fragmentos de contexto a recuperar (1-20)."] = 8,
) -> str:
    """Devuelve CONTEXTO, no una respuesta redactada.

    Deliberado: el cliente MCP ya es un modelo capaz de redactar. Llamar a otro
    LLM acá agregaría costo, latencia y una segunda oportunidad de alucinar,
    para producir texto que el cliente igual va a reescribir. Mejor darle la
    evidencia y que responda él.
    """
    try:
        d = service.recuperar(pregunta, max(1, min(k, 20)))
    except ErrorDeConsulta as e:
        return _error(e)
    except RuntimeError as e:
        return _ok({"error": f"El índice de recuperación no está disponible: {e}",
                    "sugerencia": ("Usa `precio_actual`, `serie_historica` o "
                                   "`anomalias`, que no dependen del índice.")})
    return _ok({
        "pregunta": d["pregunta"],
        "interpretacion": d["interpretacion"],
        "contexto_garantizado": [c["texto"] for c in d["garantizado"]],
        "contexto_recuperado": [c["texto"] for c in d["recuperado"]],
        "instruccion": ("Responde SOLO con estos datos. No inventes cifras y no "
                        "atribuyas causas que el contexto no respalde."),
    })


@mcp.tool(
    description=("Qué cubre el dataset: mercado, rango de fechas, cantidad de "
                 "productos y frescura. Úsala para saber si una pregunta cae "
                 "dentro de lo que estos datos pueden responder."),
    annotations=SOLO_LECTURA,
)
def cobertura() -> str:
    m = service.meta()
    return _ok({
        **m,
        "cubre": ("precios y volúmenes MAYORISTAS del Gran Mercado Mayorista de "
                  "Lima, más pollo vivo y frutas de mercados anexos"),
        "no_cubre": ("precios minoristas, otras ciudades del Perú, otros países, "
                     "y cualquier fecha fuera del rango indicado"),
        "verificacion": service.verificacion(),
    })


# --------------------------------------------------------------------------- #
# Recursos
# --------------------------------------------------------------------------- #
@mcp.resource(
    "preciovivo://catalogo",
    name="Catálogo de productos",
    description="Los productos seguidos, con su slug y precio vigente.",
    mime_type="application/json",
)
def recurso_catalogo() -> str:
    """El catálogo como recurso además de herramienta.

    Un recurso puede adjuntarse al contexto de una vez, sin gastar un turno de
    tool-call: para un catálogo de 73 nombres que el modelo va a necesitar sí o
    sí para hablar de productos, sale más barato así.
    """
    return _ok({"productos": service.listar_productos(),
                "atribucion": service.meta().get("atribucion")})


def main() -> None:
    """Punto de entrada. stdio por defecto, que es lo que usan los clientes MCP."""
    import os
    mcp.run(transport=os.environ.get("PRECIOVIVO_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
