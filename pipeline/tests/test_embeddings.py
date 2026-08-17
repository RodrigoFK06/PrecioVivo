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


# --------------------------------------------------------------------------- #
# Guard: el proveedor tiene que respetar `dimensions`
# --------------------------------------------------------------------------- #
def test_api_embedder_rechaza_dims_distintas_a_las_pedidas(monkeypatch):
    """Un proveedor OpenAI-compatible puede IGNORAR `dimensions` y devolver el
    tamaño nativo del modelo. Si eso pasara en silencio, `firma` diría
    'api:<modelo>:256' mientras los vectores tienen otro tamaño: el índice
    quedaría mal etiquetado, pesaría mucho más de lo previsto, y el guard de
    firma del sitio lo daría por bueno.
    """
    import numpy as np
    import pytest

    from preciovivo.embeddings import ApiEmbedder

    emb = ApiEmbedder.__new__(ApiEmbedder)  # sin red ni clave
    emb.dims = 256
    emb.modelo_nombre = "modelo-de-prueba"
    emb.firma = "api:modelo-de-prueba:256"

    # Lo correcto pasa tal cual.
    ok = np.zeros((3, 256), dtype=np.float32)
    assert emb._verificar_dims(ok).shape == (3, 256)

    # El tamaño nativo colado en vez del pedido, revienta con el número a la vista.
    nativo = np.zeros((3, 3072), dtype=np.float32)
    with pytest.raises(RuntimeError, match="3072"):
        emb._verificar_dims(nativo)


# --------------------------------------------------------------------------- #
# Cuota (429): esperar lo que el servidor pide, no lo que adivinaríamos
# --------------------------------------------------------------------------- #
class _Error429(Exception):
    status_code = 429


def test_espera_sugerida_lee_el_retry_delay_del_proveedor():
    """Un 429 no es un fallo: es "todavía no", y viene con el número exacto.

    El backoff genérico era de 1.5^n segundos (1, 1.5, 2.25) contra ventanas de
    cuota de ~35 s: los cuatro intentos se agotaban sin haber esperado ni una
    décima parte de lo necesario, y el backfill moría con la cuota intacta.
    """
    from preciovivo.embeddings import _espera_sugerida

    # Formato de texto de Google.
    assert _espera_sugerida(_Error429("Please retry in 34.071919166s.")) == 34.071919166
    # Bloque RetryInfo del mismo error.
    assert _espera_sugerida(_Error429("... 'retryDelay': '21s' ...")) == 21.0
    # Sin pista alguna: cae al valor por defecto, no a cero.
    assert _espera_sugerida(_Error429("boom"), por_defecto=17.0) == 17.0


def test_cabecera_retry_after_tiene_prioridad():
    from preciovivo.embeddings import _espera_sugerida

    class _Resp:
        headers = {"retry-after": "12"}

    e = _Error429("Please retry in 99s.")
    e.response = _Resp()
    assert _espera_sugerida(e) == 12.0


def test_un_429_no_se_confunde_con_un_error_permanente():
    from preciovivo.embeddings import _es_cuota, _es_error_permanente

    assert _es_cuota(_Error429("cuota"))
    assert not _es_error_permanente(_Error429("cuota"))

    class _Error400(Exception):
        status_code = 400

    # Un request inválido NO se reintenta: reintentarlo es quemar tiempo.
    assert _es_error_permanente(_Error400("modelo inexistente"))
    assert not _es_cuota(_Error400("modelo inexistente"))


# --------------------------------------------------------------------------- #
# Clasificación de 429: throttle recuperable vs cuota agotada
#
# Los tres casos son REALES, observados contra dos proveedores. La distinción
# importa porque el error es el mismo código HTTP y las consecuencias son
# opuestas: ante un throttle hay que esperar y seguir; ante una cuota agotada,
# esperar es tiempo tirado contra una pared.
# --------------------------------------------------------------------------- #
def test_clasificar_cuota_con_retry_info_explicito():
    """Gemini, límite por minuto: trae `retryDelay` y hay que hacerle caso."""
    from preciovivo.embeddings import _clasificar_cuota

    e = _Error429("... 'retryDelay': '34s' ... RESOURCE_EXHAUSTED")
    recuperable, espera = _clasificar_cuota(e)
    assert recuperable and espera == 34.0


def test_clasificar_cuota_limite_por_minuto_solo_en_el_texto():
    """Jina: sin metadatos, pero el MENSAJE dice que es por minuto.

    Este caso rompió la heurística anterior —que solo miraba si había
    RetryInfo— y abortó un backfill que únicamente necesitaba esperar.
    """
    from preciovivo.embeddings import VENTANA_MINUTO, _clasificar_cuota

    e = _Error429("Token rate limit exceeded: 100,452/100,000 tokens per minute. "
                  "Reduce batch sizes or upgrade your plan.")
    recuperable, espera = _clasificar_cuota(e)
    assert recuperable, "un límite por minuto SIEMPRE es recuperable"
    assert espera == VENTANA_MINUTO


def test_clasificar_cuota_diaria_no_es_recuperable():
    """Gemini free tier: 1.000 embeddings por DÍA. Esperar no lo arregla."""
    from preciovivo.embeddings import _clasificar_cuota

    e = _Error429("Quota exceeded for metric: embed_content_free_tier_requests, "
                  "limit: 1000 ... quotaId: "
                  "'EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier'")
    recuperable, _ = _clasificar_cuota(e)
    assert not recuperable


def test_clasificar_cuota_sin_pistas_es_conservador():
    """Sin señal, no se insiste: se falla rápido y con un mensaje que orienta."""
    from preciovivo.embeddings import _clasificar_cuota

    assert _clasificar_cuota(_Error429("algo salió mal")) == (False, 0.0)


def test_la_cabecera_retry_after_gana_a_todo():
    from preciovivo.embeddings import _clasificar_cuota

    class _Resp:
        headers = {"retry-after": "7"}

    e = _Error429("tokens per minute ... 'retryDelay': '99s'")
    e.response = _Resp()
    assert _clasificar_cuota(e) == (True, 7.0)
