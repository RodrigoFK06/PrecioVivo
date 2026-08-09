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
import time
from typing import Protocol, runtime_checkable

import numpy as np

try:  # .env (claves) — opcional, no-op sin el paquete. Igual que ai.py.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# --- Proveedor de API (OpenAI-compatible /embeddings) ---------------------
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "https://api.openai.com/v1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
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

    def _pedir(self, lote: list[str]) -> np.ndarray:
        ultimo: Exception | None = None
        for intento in range(REINTENTOS):
            try:
                resp = self._cliente.embeddings.create(
                    model=self.modelo_nombre, input=lote, dimensions=self.dims)
                return np.array([d.embedding for d in resp.data], dtype=np.float32)
            except Exception as e:  # noqa: BLE001 - se reclasifica abajo
                # Errores de configuración (auth, modelo inexistente, input
                # inválido) no se arreglan reintentando: fallar rápido y claro.
                if _es_error_permanente(e):
                    raise
                ultimo = e
                if intento < REINTENTOS - 1:
                    time.sleep(BACKOFF_BASE ** intento)
        raise RuntimeError(
            f"El proveedor de embeddings falló tras {REINTENTOS} intentos: {ultimo}"
        ) from ultimo

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dims), dtype=np.float32)
        trozos = [self._pedir(textos[i:i + EMBED_LOTE])
                  for i in range(0, len(textos), EMBED_LOTE)]
        return _normalizar(np.vstack(trozos))

    def embed_consulta(self, texto: str) -> np.ndarray:
        return _normalizar(self._pedir([texto]))[0]


def _es_error_permanente(e: Exception) -> bool:
    """True si reintentar no puede ayudar (auth, request inválido, modelo malo)."""
    codigo = getattr(e, "status_code", None) or getattr(e, "code", None)
    if isinstance(codigo, int):
        # 408 y 429 sí se reintentan; el resto de 4xx, no.
        return 400 <= codigo < 500 and codigo not in (408, 429)
    return False


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
def get_embedder(proveedor: str | None = None) -> Embedder:
    """Devuelve el embebedor configurado.

    `proveedor`: api|local|fake|auto (o PRECIOVIVO_EMBED_PROVIDER; default auto).

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
    if proveedor == "local":
        return LocalEmbedder()
    if proveedor != "auto":
        raise ValueError(f"proveedor de embeddings desconocido: {proveedor!r}")

    if _embed_api_key():
        return ApiEmbedder()
    return LocalEmbedder()
