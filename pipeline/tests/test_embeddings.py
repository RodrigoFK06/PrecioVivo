"""Pruebas de la capa de embeddings (preciovivo.embeddings).

Auto-contenidas: sin red, sin claves, sin modelo descargado. Solo se ejercita
FakeEmbedder; de LocalEmbedder y ApiEmbedder se verifica el contrato de fallo
(qué error dan cuando les falta su dependencia o su clave), que es lo que se
puede afirmar sin salir a la red.
"""
from __future__ import annotations

import numpy as np
import pytest

from preciovivo import embeddings as E


@pytest.fixture(autouse=True)
def _sin_claves(monkeypatch):
    """La selección automática no debe depender del entorno de quien corre."""
    for var in ("EMBED_API_KEY", "OPENAI_API_KEY", "PRECIOVIVO_EMBED_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #
def test_normalizar_deja_norma_unitaria():
    v = E._normalizar(np.array([[3.0, 4.0], [1.0, 0.0]]))
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)


def test_normalizar_no_produce_nan_con_vector_cero():
    """Un chunk sin tokens reconocibles da vector nulo: no debe volverse NaN,
    porque un NaN envenena todo el producto punto aguas abajo."""
    v = E._normalizar(np.zeros((1, 4)))
    assert not np.isnan(v).any()
    assert np.allclose(v, 0.0)


def test_normalizar_acepta_vector_1d():
    v = E._normalizar(np.array([3.0, 4.0]))
    assert v.shape == (1, 2)


# --------------------------------------------------------------------------- #
# FakeEmbedder
# --------------------------------------------------------------------------- #
def test_fake_es_determinista(embedder_fake):
    a = embedder_fake.embed_consulta("por qué subió la papa")
    b = embedder_fake.embed_consulta("por qué subió la papa")
    assert np.allclose(a, b)


def test_fake_devuelve_vectores_normalizados(embedder_fake):
    M = embedder_fake.embed_documentos(["la papa subió", "el tomate bajó"])
    assert M.shape == (2, embedder_fake.dims)
    assert np.allclose(np.linalg.norm(M, axis=1), 1.0)


def test_fake_lista_vacia(embedder_fake):
    M = embedder_fake.embed_documentos([])
    assert M.shape == (0, embedder_fake.dims)


def test_fake_acerca_textos_parecidos(embedder_fake):
    """No mide semántica, pero sí solapamiento léxico — suficiente para testear
    la MECÁNICA de la recuperación sin red ni modelo."""
    docs = [
        "la papa subió esta semana en el mercado mayorista",
        "el pollo vivo se vende en el mercado de aves",
    ]
    M = embedder_fake.embed_documentos(docs)
    q = embedder_fake.embed_consulta("por qué subió la papa esta semana")
    sims = M @ q
    assert sims[0] > sims[1]


def test_fake_ignora_acentos_y_mayusculas(embedder_fake):
    a = embedder_fake.embed_consulta("MARACUYÁ")
    b = embedder_fake.embed_consulta("maracuya")
    assert np.allclose(a, b)


def test_firma_declara_dimensiones(embedder_fake):
    assert embedder_fake.firma == f"fake:bolsa-hash:{embedder_fake.dims}"


def test_fake_cumple_el_protocolo(embedder_fake):
    assert isinstance(embedder_fake, E.Embedder)


# --------------------------------------------------------------------------- #
# Selección de proveedor
# --------------------------------------------------------------------------- #
def test_get_embedder_fake_explicito():
    assert isinstance(E.get_embedder("fake"), E.FakeEmbedder)


def test_get_embedder_rechaza_proveedor_desconocido():
    with pytest.raises(ValueError):
        E.get_embedder("magia")


def test_get_embedder_api_sin_clave_falla_con_mensaje_util():
    """El error debe explicar que AI_API_KEY (DeepSeek) NO sirve aquí: es
    exactamente la confusión que se va a tener."""
    with pytest.raises(RuntimeError) as exc:
        E.get_embedder("api")
    assert "EMBED_API_KEY" in str(exc.value)
    assert "DeepSeek" in str(exc.value)


def test_api_no_reusa_la_clave_de_deepseek(monkeypatch):
    """AI_API_KEY es de un proveedor sin endpoint de embeddings; usarla daría un
    404 confuso en vez de un mensaje de configuración."""
    monkeypatch.setenv("AI_API_KEY", "sk-deepseek-falsa")
    assert E._embed_api_key() is None


def test_auto_no_cae_a_fake_en_silencio(monkeypatch):
    """Un índice construido con vectores de juguete PARECE funcionar y devuelve
    basura. Sin proveedor real, hay que fallar, no degradar."""
    monkeypatch.setattr(E, "_embed_api_key", lambda: None)

    def _explota(*a, **kw):
        raise RuntimeError("model2vec no instalado")

    monkeypatch.setattr(E, "LocalEmbedder", _explota)
    with pytest.raises(RuntimeError):
        E.get_embedder("auto")


def test_auto_prefiere_api_si_hay_clave(monkeypatch):
    monkeypatch.setattr(E, "_embed_api_key", lambda: "sk-x")
    marca = object()
    monkeypatch.setattr(E, "ApiEmbedder", lambda: marca)
    assert E.get_embedder("auto") is marca


def test_provider_por_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("PRECIOVIVO_EMBED_PROVIDER", "fake")
    assert isinstance(E.get_embedder(), E.FakeEmbedder)


# --------------------------------------------------------------------------- #
# Clasificación de errores de la API (reintentos)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("codigo,permanente", [
    (401, True),    # credencial mala: reintentar no ayuda
    (404, True),    # modelo inexistente
    (422, True),    # input inválido
    (429, False),   # rate limit: sí se reintenta
    (408, False),   # timeout: sí se reintenta
    (500, False),   # error del servidor: sí se reintenta
    (503, False),
])
def test_clasificacion_de_errores(codigo, permanente):
    e = Exception("x")
    e.status_code = codigo
    assert E._es_error_permanente(e) is permanente


def test_error_sin_codigo_se_reintenta():
    """Un fallo de red no trae status: hay que asumir que es transitorio."""
    assert E._es_error_permanente(ConnectionError("sin red")) is False
