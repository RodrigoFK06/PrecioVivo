"""Adaptador de Amazon Bedrock con la forma del cliente OpenAI.

POR QUÉ UN ADAPTADOR Y NO UN CAMBIO DE `base_url`
--------------------------------------------------
`ai.py` habla con el proveedor por un cliente OpenAI-compatible: `base_url` más
`api_key`. Bedrock también publica un endpoint OpenAI-compatible, así que la
tentación es cambiar dos variables de entorno y declarar victoria.

El problema es la credencial. Ese endpoint se autentica con una API key de
Bedrock: una cadena larga y estática que hay que guardar en algún sitio, rotar
cada tantos meses y no filtrar nunca. Dentro de AWS eso es un paso atrás — el rol
de la Lambda ya da credenciales temporales que rotan solas y no existen en ningún
archivo. La credencial más segura es la que no hay.

Así que el adaptador va por `bedrock-runtime` con boto3, firmado con el rol, y a
cambio imita la forma del cliente de OpenAI en lo poco que `ai.py` usa de verdad:

    client.chat.completions.create(model=, messages=, max_tokens=, tools=,
                                   tool_choice=)
        -> .choices[0].message.content
        -> .choices[0].message.tool_calls[0].function.arguments   (str JSON)

Los cinco sitios de llamada de `ai.py` no cambian ni una línea. Eso no es
casualidad ni suerte: es lo que compra haber metido el proveedor detrás de una
interfaz en su día.

LO QUE ESTE ADAPTADOR NO ES
---------------------------
No es un cliente de OpenAI completo. Traduce exactamente lo que el proyecto usa
—un mensaje de sistema, mensajes de usuario, una función forzada y un tope de
tokens— y nada más. Un adaptador que finge implementar toda una API ajena miente
sobre lo que soporta; éste falla con AttributeError en cuanto se le pida algo que
no traduce, que es el fallo correcto.

MODELO E INVOCACIÓN
-------------------
Los Claude modernos en us-east-1 NO son invocables por su `modelId` pelado: la
API los marca `INFERENCE_PROFILE` y hay que llamarlos por el perfil (`us.` o
`global.` delante). Comprobado: `anthropic.claude-sonnet-4-5-...` da error y
`us.anthropic.claude-sonnet-4-5-...` responde. Curiosamente, el único modelo que
la API marca `ON_DEMAND` (`claude-3-haiku`) falla por estar declarado *Legacy*.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

# Perfil de inferencia, no modelId. Ver el docstring.
BEDROCK_MODELO_CHAT = os.environ.get(
    "BEDROCK_CHAT_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _a_bedrock(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Parte los mensajes al formato de `converse`.

    OpenAI mete el mensaje de sistema en la misma lista; Bedrock lo quiere en un
    parámetro aparte. Y cada contenido es una lista de bloques, no una cadena.
    """
    sistema, charla = [], []
    for m in messages:
        if m["role"] == "system":
            sistema.append({"text": m["content"]})
        else:
            charla.append({"role": m["role"], "content": [{"text": m["content"]}]})
    return sistema, charla


def _herramientas(tools: list[dict] | None, tool_choice) -> dict | None:
    """Traduce el esquema de funciones de OpenAI al `toolConfig` de Bedrock."""
    if not tools:
        return None
    specs = []
    for t in tools:
        f = t["function"]
        specs.append({"toolSpec": {
            "name": f["name"],
            "description": f.get("description", ""),
            # Bedrock envuelve el JSON Schema en {"json": ...}; por lo demás es
            # el mismo esquema, así que las definiciones de ai.py sirven tal cual.
            "inputSchema": {"json": f["parameters"]},
        }})
    cfg: dict = {"tools": specs}
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        cfg["toolChoice"] = {"tool": {"name": tool_choice["function"]["name"]}}
    return cfg


class _Completions:
    def __init__(self, cliente, modelo: str):
        self._c = cliente
        self._modelo = modelo

    def create(self, *, messages, model=None, max_tokens=1024,
               tools=None, tool_choice=None, **_ignorado):
        sistema, charla = _a_bedrock(messages)
        kwargs = {
            # `model` llega desde AI_MODEL, que en el resto del proyecto nombra
            # modelos de otro proveedor. Aquí manda el perfil de inferencia.
            "modelId": self._modelo,
            "messages": charla,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if sistema:
            kwargs["system"] = sistema
        cfg = _herramientas(tools, tool_choice)
        if cfg:
            kwargs["toolConfig"] = cfg

        r = self._c.converse(**kwargs)
        bloques = r["output"]["message"]["content"]

        texto = "".join(b["text"] for b in bloques if "text" in b) or None
        llamadas = []
        for b in bloques:
            if "toolUse" in b:
                u = b["toolUse"]
                llamadas.append(SimpleNamespace(
                    id=u.get("toolUseId"),
                    type="function",
                    # `arguments` es una CADENA JSON, no un dict: los sitios de
                    # llamada hacen json.loads sobre él. Bedrock devuelve el dict
                    # ya parseado, así que hay que volver a serializarlo. Parece
                    # tonto y es lo que mantiene el contrato.
                    function=SimpleNamespace(name=u["name"],
                                             arguments=json.dumps(u["input"],
                                                                  ensure_ascii=False)),
                ))

        mensaje = SimpleNamespace(content=texto, tool_calls=llamadas or None)
        eleccion = SimpleNamespace(message=mensaje,
                                   finish_reason=r.get("stopReason"))
        return SimpleNamespace(choices=[eleccion], usage=r.get("usage"))


class ClienteBedrock:
    """Cliente con la forma mínima que `ai.py` espera de OpenAI."""

    def __init__(self, modelo: str = BEDROCK_MODELO_CHAT,
                 region: str = BEDROCK_REGION):
        import boto3
        self._c = boto3.client("bedrock-runtime", region_name=region)
        self.modelo = modelo
        self.chat = SimpleNamespace(completions=_Completions(self._c, modelo))
