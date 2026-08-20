"""Embeddings de Precio Vivo: una interfaz, tres proveedores.

POR QUÉ TRES
------------
DeepSeek —el proveedor que usa toda la capa IA— NO tiene endpoint de embeddings
(sus docs solo documentan chat/completions). Así que los embeddings requieren un
proveedor aparte, y hay tres roles distintos que cubrir:

  ApiEmbedder    Construye el índice que se PUBLICA y embebe la consulta en
                 producción. Es obligatorio para el sitio en vivo: el índice va
                 estático en el deploy, pero la pregunta del usuario hay que
                 embeberla en tiempo de request, y Vercel no puede correr un
                 modelo local.
  LocalEmbedder  Para quien clona el repo sin claves, y para desarrollo. Usa
                 model2vec (inferencia solo-numpy, ~30 MB) en vez de
                 sentence-transformers + torch (~2,5 GB): el pipeline entero hoy
                 pesa menos que torch, y no vale meterlo por esto.
  FakeEmbedder   Tests y CI. Determinista, sin red, sin modelo, sin claves.

COMPATIBILIDAD (load-bearing)
-----------------------------
Un índice construido con un embebedor SOLO puede consultarse con el mismo. Si se
mezclan, el coseno compara espacios vectoriales distintos y la recuperación
devuelve ruido con total confianza — el peor modo de falla posible, porque no se
nota. Por eso cada embebedor expone `firma`, que se guarda en el índice y se
verifica al consultar. Ver vectorstore.IndiceMeta.

NORMALIZACIÓN
-------------
Todos devuelven vectores L2-normalizados. Así el coseno es un producto punto, y
numpy, sqlite-vec, pgvector y el TypeScript del sitio calculan todos lo mismo sin
que ninguno tenga que acordarse de normalizar.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Protocol, runtime_checkable

import numpy as np

try:  # .env (claves) — opcional, no-op sin el paquete. Igual que ai.py.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# --- Proveedor de API (OpenAI-compatible /embeddings) ---------------------
# LOS DEFAULTS DESCRIBEN EL DESPLIEGUE REAL, no una opción genérica.
#
# Antes caían a OpenAI/text-embedding-3-small, que no se usa en ningún sitio de
# este proyecto: ni en Vercel, ni en la Lambda de indexado, ni en el .env del
# mantenedor. Un default que nadie usa no es neutral, es una trampa: si alguna
# vez falta la variable, el sistema arranca con una firma de embebedor DISTINTA
# a la del índice publicado, `rag.ts` captura el desajuste, cae al piso
# determinista y sigue etiquetando la respuesta como 'llm-rag'.
#
# Con estos defaults, perder la variable degrada a "falta la clave" —visible—
# en vez de a "recuperación vectorial apagada en silencio".
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "https://api.jina.ai/v1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "jina-embeddings-v3")
# 256 dims: text-embedding-3-* admite truncar dimensiones con calidad casi
# intacta, y a 256 el índice publicado pesa ~4x menos. Ver README.
EMBED_DIMS = int(os.environ.get("EMBED_DIMS", "256"))
EMBED_LOTE = int(os.environ.get("EMBED_LOTE", "128"))

# --- Proveedor local (model2vec, inferencia numpy) ------------------------
LOCAL_MODEL = os.environ.get(
    "PRECIOVIVO_EMBED_LOCAL_MODEL", "minishlab/potion-multilingual-128M")

# Reintentos de la API: backoff exponencial, visible y acotado.
REINTENTOS = 4
BACKOFF_BASE = 1.5
# Reintentos ante 429 (cuota). Van aparte y son muchos más porque NO son un
# fallo: el servidor dice cuánto esperar y esperar es la respuesta correcta.
# Con backoff genérico de 1-2 s contra una ventana de cuota de 60 s, los cuatro
# intentos se agotan sin haber esperado ni la décima parte de lo necesario.
REINTENTOS_CUOTA = int(os.environ.get("EMBED_REINTENTOS_CUOTA", "40"))
# Ritmo proactivo, en TEXTOS por minuto (0 = sin límite). Evita pedir de más y
# comerse la cuota en un pico: es preferible ir al ritmo permitido que hacer
# rebotar cada petición contra un 429.
EMBED_RPM = int(os.environ.get("EMBED_RPM", "0"))
# Margen sobre la espera que sugiere el servidor, para no volver justo al borde.
MARGEN_CUOTA = 1.0


def _embed_api_key() -> str | None:
    """Clave del proveedor de EMBEDDINGS.

    Deliberadamente NO cae a AI_API_KEY: esa es la clave de DeepSeek, que no
    tiene endpoint de embeddings. Reusarla produciría un 404 confuso en vez de
    un mensaje claro de configuración.
    """
    return os.environ.get("EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _normalizar(v: np.ndarray) -> np.ndarray:
    """L2-normaliza por filas. Vectores nulos quedan nulos (no NaN)."""
    v = np.asarray(v, dtype=np.float32)
    if v.ndim == 1:
        v = v.reshape(1, -1)
    normas = np.linalg.norm(v, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return (v / normas).astype(np.float32)


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #
@runtime_checkable
class Embedder(Protocol):
    """Contrato de un embebedor.

    `embed_documentos` y `embed_consulta` están separados a propósito: algunos
    modelos (la familia e5, entre otros) esperan prefijos distintos para el
    documento y para la pregunta, y esa asimetría debe vivir dentro del
    embebedor, no repartida por el código que lo usa.
    """
    firma: str
    dims: int

    def embed_documentos(self, textos: list[str]) -> np.ndarray: ...

    def embed_consulta(self, texto: str) -> np.ndarray: ...


# --------------------------------------------------------------------------- #
# 1) Fake determinista — tests y CI
# --------------------------------------------------------------------------- #
class FakeEmbedder:
    """Embebedor determinista sin red ni modelo, para tests.

    No es aleatorio: proyecta una bolsa de tokens hasheados. Textos que comparten
    palabras quedan cerca, textos disjuntos quedan lejos. Eso permite testear la
    MECÁNICA de la recuperación (que el vecino correcto sale primero) sin
    depender de una red ni de un modelo descargado. NO sirve para medir calidad
    semántica — para eso están las evaluaciones con un embebedor real.
    """

    def __init__(self, dims: int = 64):
        self.dims = dims
        self.firma = f"fake:bolsa-hash:{dims}"

    def _vector(self, texto: str) -> np.ndarray:
        v = np.zeros(self.dims, dtype=np.float32)
        for token in _tokenizar(texto):
            h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dims
            signo = 1.0 if h[4] % 2 == 0 else -1.0
            v[idx] += signo
        return v

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dims), dtype=np.float32)
        return _normalizar(np.stack([self._vector(t) for t in textos]))

    def embed_consulta(self, texto: str) -> np.ndarray:
        return _normalizar(self._vector(texto))[0]


def _tokenizar(texto: str) -> list[str]:
    """Tokenización mínima y estable para FakeEmbedder (no para producción)."""
    import re
    import unicodedata

    t = "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                if not unicodedata.combining(c))
    return [w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 2]


# --------------------------------------------------------------------------- #
# 2) Local — model2vec (numpy puro, sin torch)
# --------------------------------------------------------------------------- #
class LocalEmbedder:
    """Embebedor local con model2vec. Sin clave, sin red tras la primera carga.

    model2vec usa embeddings estáticos destilados: la inferencia es una búsqueda
    en tabla más un promedio, en numpy. No necesita torch. A cambio, no modela
    contexto — para el texto de nuestros chunks (frases descriptivas cortas y
    muy consistentes en formato) es un intercambio razonable, y quien quiera
    máxima calidad puede usar ApiEmbedder.
    """

    def __init__(self, modelo: str = LOCAL_MODEL):
        try:
            from model2vec import StaticModel
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError(
                "El embebedor local necesita model2vec: pip install model2vec. "
                "Alternativas: definir EMBED_API_KEY para usar el proveedor de "
                "API, o PRECIOVIVO_EMBED_PROVIDER=fake en tests."
            ) from e
        self.modelo_nombre = modelo
        self._modelo = StaticModel.from_pretrained(modelo)
        self.dims = int(self._modelo.dim)
        self.firma = f"local:{modelo}:{self.dims}"

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dims), dtype=np.float32)
        return _normalizar(self._modelo.encode(textos))

    def embed_consulta(self, texto: str) -> np.ndarray:
        return _normalizar(self._modelo.encode([texto]))[0]


# --------------------------------------------------------------------------- #
# 3) API — OpenAI-compatible /embeddings
# --------------------------------------------------------------------------- #
class ApiEmbedder:
    """Embebedor vía API OpenAI-compatible. Construye el índice que se publica.

    Reintentos con backoff exponencial explícito: un fallo de red a mitad de un
    backfill de miles de chunks no debe obligar a rehacerlo entero, y un error de
    autenticación debe fallar RÁPIDO y fuerte en vez de reintentarse cuatro veces.
    """

    def __init__(self, modelo: str = EMBED_MODEL, dims: int = EMBED_DIMS,
                 base_url: str = EMBED_BASE_URL, api_key: str | None = None):
        key = api_key or _embed_api_key()
        if not key:
            raise RuntimeError(
                "Falta EMBED_API_KEY (o OPENAI_API_KEY) para el embebedor de API. "
                "Ojo: AI_API_KEY es la clave de DeepSeek, que no ofrece embeddings.")
        try:
            from openai import OpenAI
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError("El embebedor de API necesita: pip install openai") from e
        self._cliente = OpenAI(api_key=key, base_url=base_url)
        self.modelo_nombre = modelo
        self.dims = dims
        self.firma = f"api:{modelo}:{dims}"
        self._ultimo_envio = 0.0

    def _verificar_dims(self, arr: np.ndarray) -> np.ndarray:
        """El proveedor debe devolver EXACTAMENTE las dimensiones que se pidieron.

        `dimensions` es un parámetro de OpenAI que no todos los proveedores
        compatibles implementan; algunos lo IGNORAN en silencio y devuelven el
        tamaño nativo del modelo. Eso sería veneno: `firma` diría
        'api:<modelo>:256' —porque la firma se arma con lo pedido— mientras los
        vectores tienen otro tamaño. El índice quedaría etiquetado con una
        dimensión que no es la suya, el artefacto pesaría 10x lo previsto, y el
        sitio validaría la firma como correcta.

        Mejor romper acá, con el número real a la vista.
        """
        if arr.ndim == 2 and arr.shape[1] != self.dims:
            raise RuntimeError(
                f"El proveedor devolvió vectores de {arr.shape[1]} dimensiones y "
                f"se pidieron {self.dims}: está ignorando el parámetro "
                f"`dimensions`.\n"
                f"Opciones: define EMBED_DIMS={arr.shape[1]} (ojo: el índice "
                f"publicado crece en proporción), o usa un modelo/proveedor que "
                f"respete `dimensions` (los text-embedding-3-* de OpenAI lo "
                f"hacen).")
        return arr

    def _esperar_turno(self, n_textos: int) -> None:
        """Espacia las peticiones para respetar EMBED_RPM (textos por minuto)."""
        if EMBED_RPM <= 0:
            return
        minimo = 60.0 * n_textos / EMBED_RPM
        transcurrido = time.monotonic() - self._ultimo_envio
        if self._ultimo_envio and transcurrido < minimo:
            time.sleep(minimo - transcurrido)

    def _pedir(self, lote: list[str]) -> np.ndarray:
        ultimo: Exception | None = None
        intentos_cuota = 0
        intento = 0
        while True:
            self._esperar_turno(len(lote))
            try:
                self._ultimo_envio = time.monotonic()
                resp = self._cliente.embeddings.create(
                    model=self.modelo_nombre, input=lote, dimensions=self.dims)
                return self._verificar_dims(
                    np.array([d.embedding for d in resp.data], dtype=np.float32))
            except Exception as e:  # noqa: BLE001 - se reclasifica abajo
                # Errores de configuración (auth, modelo inexistente, input
                # inválido) no se arreglan reintentando: fallar rápido y claro.
                if _es_error_permanente(e):
                    raise
                ultimo = e
                if _es_cuota(e):
                    recuperable, espera = _clasificar_cuota(e)
                    if not recuperable:
                        # Cuota agotada (o límite que el proveedor no explica).
                        # Insistir no la devuelve; solo gasta tiempo contra una
                        # pared. Se corta ya y se dice qué es.
                        raise RuntimeError(
                            "Cuota del proveedor de embeddings AGOTADA, o límite "
                            "que no dice cuándo se libera.\n"
                            "No se reintenta porque no hay nada que esperar. "
                            "Opciones: bajar EMBED_LOTE y EMBED_RPM y retomar "
                            "más tarde (la caché conserva lo hecho), habilitar "
                            "facturación en el proveedor, o cambiar a uno con "
                            f"cuota suficiente.\nDetalle: {e}") from e
                    # Recuperable: el proveedor dice "todavía no". Se espera lo
                    # que PIDE, o la ventana si solo dice que es por minuto.
                    intentos_cuota += 1
                    if intentos_cuota > REINTENTOS_CUOTA:
                        break
                    # Anunciar la espera: 40 esperas mudas de 30 s son 20 minutos
                    # en los que el proceso parece colgado.
                    print(f"  cuota: esperando {espera:.0f}s "
                          f"({intentos_cuota}/{REINTENTOS_CUOTA})", flush=True)
                    time.sleep(espera + MARGEN_CUOTA)
                    continue
                intento += 1
                if intento >= REINTENTOS:
                    break
                time.sleep(BACKOFF_BASE ** (intento - 1))
        detalle = (f"{REINTENTOS_CUOTA} esperas de cuota" if intentos_cuota
                   else f"{REINTENTOS} intentos")
        raise RuntimeError(
            f"El proveedor de embeddings falló tras {detalle}: {ultimo}"
        ) from ultimo

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dims), dtype=np.float32)
        lotes = list(range(0, len(textos), EMBED_LOTE))
        # Un backfill limitado por cuota dura más de una hora. Sin señal de vida
        # es indistinguible de un proceso colgado, y quien lo mira lo mata.
        verboso = len(lotes) > 5
        t0 = time.monotonic()
        trozos = []
        for k, i in enumerate(lotes, start=1):
            trozos.append(self._pedir(textos[i:i + EMBED_LOTE]))
            if verboso:
                hechos = min(i + EMBED_LOTE, len(textos))
                transcurrido = time.monotonic() - t0
                restante = transcurrido / hechos * (len(textos) - hechos)
                print(f"  embeddings: {hechos:>6,}/{len(textos):,} "
                      f"({100 * hechos / len(textos):5.1f}%) · lote {k}/{len(lotes)} · "
                      f"faltan ~{restante / 60:.0f} min", flush=True)
        return _normalizar(np.vstack(trozos))

    def embed_consulta(self, texto: str) -> np.ndarray:
        return _normalizar(self._pedir([texto]))[0]


def _es_error_permanente(e: Exception) -> bool:
    """True si reintentar no puede ayudar (auth, request inválido, modelo malo)."""
    codigo = _codigo_http(e)
    if isinstance(codigo, int):
        # 408 y 429 sí se reintentan; el resto de 4xx, no.
        return 400 <= codigo < 500 and codigo not in (408, 429)
    return False


def _codigo_http(e: Exception) -> int | None:
    codigo = getattr(e, "status_code", None) or getattr(e, "code", None)
    return codigo if isinstance(codigo, int) else None


def _es_cuota(e: Exception) -> bool:
    return _codigo_http(e) == 429


# Duración que se asume para una ventana de límite POR MINUTO cuando el
# proveedor la anuncia pero no dice cuánto falta. 60 s cubre la ventana entera.
VENTANA_MINUTO = 60.0

# Un 429 puede ser dos cosas MUY distintas y no todos los proveedores lo dicen
# igual. Lo que se ha observado:
#   Gemini, límite por minuto : trae `RetryInfo` con `retryDelay: 34s`.
#   Gemini, cuota diaria      : 429 pelado + "check your plan and billing details".
#   Jina, límite por minuto   : sin metadatos, pero el TEXTO lo dice:
#                               "Token rate limit exceeded: 100,452/100,000 tokens
#                                per minute. Reduce batch sizes..."
# De ahí que haya que mirar también el mensaje: una heurística basada solo en la
# presencia de RetryInfo trataba el caso de Jina —perfectamente recuperable—
# como cuota agotada, y abortaba un backfill que solo necesitaba esperar.
_RE_POR_VENTANA = re.compile(
    r"per[-\s]?minute|per[-\s]?second|por minuto|rate limit exceeded|"
    r"requests? per|tokens per",
    re.IGNORECASE)
_RE_POR_DIA = re.compile(r"per[-\s]?day|perday|por d[ií]a|daily", re.IGNORECASE)


def _clasificar_cuota(e: Exception) -> tuple[bool, float]:
    """Un 429 -> (¿se puede reintentar?, cuántos segundos esperar).

    El orden de las reglas es el orden de fiabilidad de la señal:
      1. El servidor dice CUÁNDO. Es lo mejor que puede pasar; se le hace caso.
      2. Dice "por día". No hay nada que esperar en esta corrida.
      3. Dice "por minuto" / "rate limit". Ventana corta: se espera y se sigue.
      4. No dice nada. Conservador: no insistir contra una pared desconocida.
    """
    resp = getattr(e, "response", None)
    cabeceras = getattr(resp, "headers", None)
    if cabeceras:
        crudo = cabeceras.get("retry-after") or cabeceras.get("Retry-After")
        if crudo:
            try:
                return True, float(crudo)
            except (TypeError, ValueError):
                pass
    texto = str(e)
    if re.search(r"retry in [\d.]+s|'retryDelay'", texto):
        return True, _espera_sugerida(e)
    if _RE_POR_DIA.search(texto):
        return False, 0.0
    if _RE_POR_VENTANA.search(texto):
        return True, VENTANA_MINUTO
    return False, 0.0


def _espera_sugerida(e: Exception, por_defecto: float = 30.0) -> float:
    """Segundos que el propio servidor pide esperar ante un 429.

    Se lee de donde el proveedor la ponga: la cabecera `Retry-After`, el bloque
    `RetryInfo` de Google, o el texto del mensaje ("Please retry in 34.07s").
    Adivinar un backoff cuando la respuesta trae el número exacto es tirar
    intentos: con 1-2 s contra una ventana de cuota de 60 s no se llega nunca.
    """
    resp = getattr(e, "response", None)
    cabeceras = getattr(resp, "headers", None)
    if cabeceras:
        crudo = cabeceras.get("retry-after") or cabeceras.get("Retry-After")
        if crudo:
            try:
                return float(crudo)
            except (TypeError, ValueError):
                pass
    m = re.search(r"retry in ([\d.]+)s|'retryDelay':\s*'([\d.]+)s'", str(e))
    if m:
        return float(m.group(1) or m.group(2))
    return por_defecto


# --------------------------------------------------------------------------- #
# Bedrock (rol de IAM, sin credenciales)
# --------------------------------------------------------------------------- #
BEDROCK_MODELO = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
# LA CUOTA, que es lo que manda aquí y no se puede negociar:
#
#   aws service-quotas list-service-quotas --service-code bedrock
#   "On-demand ... requests per minute for Amazon Titan Text Embeddings V2"
#       Value: 60   Adjustable: FALSE
#
# Sesenta peticiones por minuto y NO ajustable: no hay formulario que pedir.
# Titan embebe UN texto por llamada, así que son 60 chunks/minuto y el
# paralelismo no sirve de nada — más hilos solo producen ThrottlingException
# más rápido. Se aprendió por las malas: con 16 hilos, el backfill murió con
# "reached max retries: 4".
#
# Por eso 4 hilos (para tapar la latencia de red, no para ir más rápido) y un
# cubo de fichas global que impone el ritmo de verdad. 55 y no 60: la cuota se
# mide en la ventana del servidor, no en la nuestra, y apurarla hasta el borde
# solo cambia throttling por reintentos.
BEDROCK_HILOS = int(os.environ.get("BEDROCK_EMBED_HILOS", "4"))
BEDROCK_RPM = int(os.environ.get("BEDROCK_EMBED_RPM", "55"))


class BedrockEmbedder:
    """Embebedor vía Amazon Bedrock, autenticado con el rol de IAM.

    POR QUÉ EXISTE, QUE NO ES "PORQUE AWS"
    ---------------------------------------
    `ApiEmbedder` necesita una clave de API que hay que guardar, rotar y no
    filtrar. Dentro de AWS, este embebedor no necesita ninguna: boto3 firma con
    las credenciales temporales del rol de la Lambda, que rotan solas y no
    existen en ningún archivo. La credencial más segura es la que no hay.

    Fuera de AWS sigue haciendo falta un par de claves, así que esto NO sustituye
    a `ApiEmbedder` — lo acompaña. `get_embedder` elige.

    LA FIRMA CAMBIA, Y ESO ES EL PUNTO
    -----------------------------------
    `bedrock:amazon.titan-embed-text-v2:0:256` no es
    `api:jina-embeddings-v3:256`. Son espacios vectoriales distintos y el coseno
    entre ellos no significa nada. El guard de firma existe justo para que
    mezclarlos sea imposible en vez de ser un fallo silencioso: cambiar de
    embebedor OBLIGA a reconstruir el índice entero.

    Titan v2 acepta `dimensions` de verdad (verificado: 256, 512 y 1024 devuelven
    exactamente ese tamaño) y normaliza él mismo, pero se re-normaliza igual: el
    invariante de este módulo es que TODOS los embebedores devuelven L2=1, y
    depender de que el proveedor lo cumpla es depender de su documentación.
    """

    def __init__(self, modelo: str = BEDROCK_MODELO, dims: int = EMBED_DIMS,
                 region: str = BEDROCK_REGION):
        try:
            import boto3
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError("El embebedor de Bedrock necesita: pip install boto3") from e
        import threading
        self._cliente = boto3.client("bedrock-runtime", region_name=region)
        self.modelo_nombre = modelo
        self.dims = dims
        self.firma = f"bedrock:{modelo}:{dims}"
        self._cerrojo = threading.Lock()
        self._siguiente = 0.0

    def _esperar_turno(self) -> None:
        """Cubo de fichas global: reparte las peticiones a BEDROCK_RPM por minuto.

        Va con cerrojo porque el ritmo tiene que ser del PROCESO, no de cada
        hilo: cuatro hilos esperando cada uno su intervalo darían cuatro veces
        la tasa pretendida, que es justo cómo se llega al 429.
        """
        if BEDROCK_RPM <= 0:
            return
        intervalo = 60.0 / BEDROCK_RPM
        with self._cerrojo:
            ahora = time.monotonic()
            arranque = max(ahora, self._siguiente)
            self._siguiente = arranque + intervalo
        espera = arranque - time.monotonic()
        if espera > 0:
            time.sleep(espera)

    def _uno(self, texto: str) -> np.ndarray:
        import json as _json
        cuerpo = _json.dumps({"inputText": texto, "dimensions": self.dims,
                              "normalize": True})
        for intento in range(6):
            self._esperar_turno()
            try:
                r = self._cliente.invoke_model(modelId=self.modelo_nombre, body=cuerpo)
                v = _json.loads(r["body"].read())["embedding"]
                return np.asarray(v, dtype=np.float32)
            except Exception as e:  # noqa: BLE001
                codigo = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                # Solo el throttling se reintenta. Un modelo inexistente o un
                # permiso que falta no se arreglan esperando, y reintentarlos
                # cuatro veces solo retrasa el mensaje de error útil.
                if codigo not in ("ThrottlingException", "TooManyRequestsException",
                                  "ServiceUnavailableException", "ModelTimeoutException"):
                    raise
                if intento == 5:
                    raise
                time.sleep(2.0 * (2 ** intento))
        raise RuntimeError("inalcanzable")

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dims), dtype=np.float32)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=BEDROCK_HILOS) as pool:
            vectores = list(pool.map(self._uno, textos))
        arr = np.vstack(vectores)
        return _normalizar(self._verificar_dims(arr))

    def embed_consulta(self, texto: str) -> np.ndarray:
        arr = self._uno(texto).reshape(1, -1)
        return _normalizar(self._verificar_dims(arr))[0]

    # Mismo guard que ApiEmbedder: si el proveedor ignora `dimensions` y devuelve
    # su tamaño nativo, la firma diría 256 mientras los vectores tienen 1024.
    _verificar_dims = ApiEmbedder._verificar_dims


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
def get_embedder(proveedor: str | None = None) -> Embedder:
    """Devuelve el embebedor configurado.

    `proveedor`: api|bedrock|local|fake|auto (o PRECIOVIVO_EMBED_PROVIDER;
    default auto).

    En modo auto se elige API si hay clave, si no local. NO cae a fake en
    silencio: un índice construido con vectores de juguete parece funcionar y
    devuelve basura. Si nada está disponible, se lanza un error que dice qué
    configurar.
    """
    proveedor = (proveedor or os.environ.get("PRECIOVIVO_EMBED_PROVIDER", "auto")).lower()

    if proveedor == "fake":
        return FakeEmbedder()
    if proveedor == "api":
        return ApiEmbedder()
    if proveedor == "bedrock":
        return BedrockEmbedder()
    if proveedor == "local":
        return LocalEmbedder()
    if proveedor != "auto":
        raise ValueError(f"proveedor de embeddings desconocido: {proveedor!r}")

    if _embed_api_key():
        return ApiEmbedder()
    return LocalEmbedder()
