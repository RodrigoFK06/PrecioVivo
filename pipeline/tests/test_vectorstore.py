"""Pruebas del índice vectorial (preciovivo.vectorstore).

La prueba central es la de ORÁCULO: `NumpyIndex` hace k-NN exacto, así que
`SqliteVecIndex` (y `PgVectorIndex`, cuando haya Postgres) tienen que devolver
exactamente los mismos vecinos. Tener una implementación obviamente correcta
contra la cual validar las optimizadas es lo que hace que valga la pena tener
tres backends.

PgVectorIndex está marcado `skipif`: en esta máquina no hay Postgres y no
afirmamos que funcione hasta poder ejecutarlo (llega con el docker-compose del
bloque 5).
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from preciovivo.corpus import Chunk, build_corpus
from preciovivo.vectorstore import (
    Filtro,
    IndiceMeta,
    NumpyIndex,
    PgVectorIndex,
    SqliteVecIndex,
    get_indice,
    huella_corpus,
)

BACKENDS_LOCALES = ["numpy", "sqlite"]

# Tolerancia de la comparación contra el oráculo. numpy hace el producto punto
# directo y sqlite-vec devuelve distancia L2 que se convierte con 1 - d²/2: la
# conversión amplifica el redondeo de float32 hasta ~1e-6.
TOL = 1e-5


def _indice(backend: str):
    return NumpyIndex() if backend == "numpy" else SqliteVecIndex(":memory:")


def assert_mismo_ranking(a, b, contexto: str = ""):
    """Verifica que dos backends devuelven resultados IGUAL DE RELEVANTES.

    No exige ids idénticos, y no es laxitud: exigirlo sería exigir algo que
    float32 no puede dar. Los dos backends calculan el mismo coseno por caminos
    distintos, así que sus scores difieren en ~1e-6; cuando dos chunks empatan
    dentro de ese margen, cuál sale primero es arbitrario y da igual.

    Lo que sí se exige, y es la propiedad que importa:
      - misma cantidad de resultados;
      - mismo score en cada posición (dentro de tolerancia);
      - si un id difiere, el score de esa posición coincide igual — o sea, se
        eligió otro chunk EMPATADO, no uno peor.
    """
    assert len(a) == len(b), f"distinta cantidad de resultados {contexto}"
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        assert x.score == pytest.approx(y.score, abs=TOL), (
            f"score distinto en la posición {i} {contexto}: "
            f"{x.chunk.id}={x.score} contra {y.chunk.id}={y.score}")


@pytest.fixture
def corpus_y_vectores(snapshot_sintetico, embedder_fake):
    chunks = build_corpus(snapshot_sintetico, "semana")
    vectores = embedder_fake.embed_documentos([c.texto for c in chunks])
    return chunks, vectores, embedder_fake


# --------------------------------------------------------------------------- #
# La prueba que justifica tener tres backends
# --------------------------------------------------------------------------- #
def test_sqlite_coincide_con_el_oraculo_numpy(corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    ni, si = NumpyIndex(), SqliteVecIndex(":memory:")
    for idx in (ni, si):
        idx.construir(chunks, vectores, "semana", emb.firma)

    preguntas = [
        "por qué subió la papa esta semana", "cuánto cuesta el tomate",
        "anomalías de precio", "cómo estuvo el mercado", "papaya en enero",
        "ingreso en toneladas", "precio promedio histórico",
    ]
    for p in preguntas:
        q = emb.embed_consulta(p)
        for k in (1, 5, 10, 25):
            assert_mismo_ranking(ni.buscar(q, k=k), si.buscar(q, k=k),
                                 f"(k={k}, pregunta={p!r})")
    si.cerrar()


def test_sqlite_coincide_con_el_oraculo_con_filtros(corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    ni, si = NumpyIndex(), SqliteVecIndex(":memory:")
    for idx in (ni, si):
        idx.construir(chunks, vectores, "semana", emb.firma)

    filtros = [
        Filtro(slugs=frozenset({"papa-blanca"})),
        Filtro(slugs=frozenset({"papa-blanca", "tomate"})),
        Filtro(tipos=frozenset({"producto-periodo"})),
        Filtro(fecha_desde="2026-02-01"),
        Filtro(fecha_hasta="2026-01-31"),
        Filtro(slugs=frozenset({"tomate"}), fecha_desde="2026-02-01"),
    ]
    q = emb.embed_consulta("precio de la papa esta semana")
    for f in filtros:
        a = ni.buscar(q, k=8, filtro=f)
        b = si.buscar(q, k=8, filtro=f)
        assert_mismo_ranking(a, b, f"(filtro={f})")
        # El pre-filtro sí tiene que ser EXACTO: no es aritmética de punto
        # flotante, es una condición booleana. Ambos backends deben respetarla.
        for r in a + b:
            assert f.acepta(r.chunk), f"{r.chunk.id} no cumple {f}"
    si.cerrar()


def test_scores_coinciden_entre_backends(corpus_y_vectores):
    """Los dos calculan el mismo coseno por caminos distintos (producto punto
    contra conversión desde L2). Deben coincidir salvo ruido de float32."""
    chunks, vectores, emb = corpus_y_vectores
    ni, si = NumpyIndex(), SqliteVecIndex(":memory:")
    for idx in (ni, si):
        idx.construir(chunks, vectores, "semana", emb.firma)
    q = emb.embed_consulta("papa blanca")
    assert_mismo_ranking(ni.buscar(q, k=5), si.buscar(q, k=5))
    si.cerrar()


# --------------------------------------------------------------------------- #
# Reproducibilidad del orden
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_orden_reproducible_entre_llamadas(backend, corpus_y_vectores):
    """Sin orden total, recall@k oscilaría entre corridas sin que nada cambie."""
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    q = emb.embed_consulta("papa")
    primero = [r.chunk.id for r in idx.buscar(q, k=10)]
    for _ in range(5):
        assert [r.chunk.id for r in idx.buscar(q, k=10)] == primero
    idx.cerrar()


@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_scores_no_crecen(backend, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    scores = [r.score for r in idx.buscar(emb.embed_consulta("tomate"), k=10)]
    assert scores == sorted(scores, reverse=True)
    idx.cerrar()


# --------------------------------------------------------------------------- #
# Guard de compatibilidad — el modo de falla silencioso
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_embebedor_incompatible_falla_ruidosamente(backend, corpus_y_vectores):
    """Consultar con otro embebedor compara espacios distintos y devolvería
    ruido con total confianza. Tiene que romper, no degradar."""
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    with pytest.raises(RuntimeError) as exc:
        idx.verificar_compatible("api:text-embedding-3-small:256")
    assert "incompatible" in str(exc.value).lower()
    idx.verificar_compatible(emb.firma)  # el correcto no lanza
    idx.cerrar()


def test_verificar_compatible_con_indice_vacio():
    with pytest.raises(RuntimeError):
        NumpyIndex().verificar_compatible("cualquiera")


def test_huella_detecta_corpus_cambiado(snapshot_sintetico):
    chunks = build_corpus(snapshot_sintetico, "semana")
    h1 = huella_corpus(chunks)
    assert h1 == huella_corpus(list(reversed(chunks)))  # no depende del orden
    modificado = list(chunks)
    modificado[0] = Chunk(**{**modificado[0].to_dict(), "texto": "otra cosa"})
    assert huella_corpus(modificado) != h1


# --------------------------------------------------------------------------- #
# Filtro
# --------------------------------------------------------------------------- #
def _chunk(**kw) -> Chunk:
    base = dict(id="x", tipo="producto-periodo", texto="t", slug="papa-blanca",
                producto="Papa Blanca", mercado="GMML",
                fecha_inicio="2026-01-05", fecha_fin="2026-01-09")
    base.update(kw)
    return Chunk(**base)


def test_filtro_vacio_acepta_todo():
    assert Filtro().vacio()
    assert Filtro().acepta(_chunk())


def test_filtro_de_fechas_es_por_solapamiento():
    """Un chunk cubre un RANGO. Preguntar por el 7 de enero debe traer la semana
    que lo contiene, no descartarla por empezar el 5."""
    c = _chunk(fecha_inicio="2026-01-05", fecha_fin="2026-01-09")
    assert Filtro(fecha_desde="2026-01-07", fecha_hasta="2026-01-07").acepta(c)
    assert Filtro(fecha_desde="2026-01-09").acepta(c)
    assert Filtro(fecha_hasta="2026-01-05").acepta(c)
    assert not Filtro(fecha_desde="2026-01-10").acepta(c)
    assert not Filtro(fecha_hasta="2026-01-04").acepta(c)


def test_filtro_por_slug_y_tipo():
    c = _chunk()
    assert Filtro(slugs=frozenset({"papa-blanca"})).acepta(c)
    assert not Filtro(slugs=frozenset({"tomate"})).acepta(c)
    assert Filtro(tipos=frozenset({"producto-periodo"})).acepta(c)
    assert not Filtro(tipos=frozenset({"producto-perfil"})).acepta(c)


@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_filtro_sin_coincidencias_devuelve_vacio(backend, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    f = Filtro(slugs=frozenset({"producto-que-no-existe"}))
    assert idx.buscar(emb.embed_consulta("papa"), k=5, filtro=f) == []
    idx.cerrar()


# --------------------------------------------------------------------------- #
# Casos borde
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_indice_vacio(backend, embedder_fake):
    idx = _indice(backend)
    idx.construir([], np.zeros((0, embedder_fake.dims), dtype=np.float32),
                  "semana", embedder_fake.firma)
    assert idx.buscar(embedder_fake.embed_consulta("papa"), k=5) == []
    idx.cerrar()


@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_k_mayor_que_el_corpus(backend, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    res = idx.buscar(emb.embed_consulta("papa"), k=len(chunks) + 50)
    assert len(res) == len(chunks)
    idx.cerrar()


def test_desajuste_chunks_vectores(corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    with pytest.raises(ValueError, match="desajuste"):
        NumpyIndex().construir(chunks[:-1], vectores, "semana", emb.firma)


def test_dimension_de_consulta_incorrecta(corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = NumpyIndex()
    idx.construir(chunks, vectores, "semana", emb.firma)
    with pytest.raises(ValueError, match="dims"):
        idx.buscar(np.zeros(7, dtype=np.float32), k=3)


# --------------------------------------------------------------------------- #
# Metadatos y persistencia
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("backend", BACKENDS_LOCALES)
def test_meta_describe_el_indice(backend, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = _indice(backend)
    idx.construir(chunks, vectores, "semana", emb.firma)
    assert idx.meta.n_chunks == len(chunks)
    assert idx.meta.dims == emb.dims
    assert idx.meta.granularidad == "semana"
    assert idx.meta.firma_embedder == emb.firma
    assert idx.meta.huella_corpus == huella_corpus(chunks)
    idx.cerrar()


def test_numpy_persiste_y_recupera(tmp_path, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    idx = NumpyIndex()
    idx.construir(chunks, vectores, "semana", emb.firma)
    ruta = tmp_path / "indice"
    idx.guardar(ruta)

    q = emb.embed_consulta("papa blanca esta semana")
    esperado = [r.chunk.id for r in idx.buscar(q, k=10)]

    otro = NumpyIndex().cargar(ruta)
    assert otro.meta.huella_corpus == idx.meta.huella_corpus
    assert otro.meta.firma_embedder == emb.firma
    assert [r.chunk.id for r in otro.buscar(q, k=10)] == esperado


def test_sqlite_persiste_entre_conexiones(tmp_path, corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    ruta = tmp_path / "rag.db"
    uno = SqliteVecIndex(ruta)
    uno.construir(chunks, vectores, "semana", emb.firma)
    q = emb.embed_consulta("tomate")
    esperado = [r.chunk.id for r in uno.buscar(q, k=5)]
    uno.cerrar()

    dos = SqliteVecIndex(ruta)
    assert dos.meta is not None
    assert dos.meta.n_chunks == len(chunks)
    assert [r.chunk.id for r in dos.buscar(q, k=5)] == esperado
    dos.cerrar()


def test_meta_ida_y_vuelta():
    m = IndiceMeta(firma_embedder="fake:x:8", dims=8, granularidad="semana",
                   n_chunks=3, huella_corpus="abc", construido_en="2026-01-01T00:00:00+00:00")
    assert IndiceMeta.from_dict(m.to_dict()) == m


# --------------------------------------------------------------------------- #
# Selección de backend
# --------------------------------------------------------------------------- #
def test_get_indice_default_es_numpy(monkeypatch):
    monkeypatch.delenv("PRECIOVIVO_INDEX_BACKEND", raising=False)
    assert isinstance(get_indice(), NumpyIndex)


def test_get_indice_backend_desconocido():
    with pytest.raises(ValueError):
        get_indice("chroma")


def test_pgvector_sin_dsn_falla_con_mensaje_util(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc:
        PgVectorIndex()
    assert "DATABASE_URL" in str(exc.value)


# --------------------------------------------------------------------------- #
# pgvector contra un Postgres real (se activa con el docker-compose del bloque 5)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                    reason="requiere un Postgres con pgvector (DATABASE_URL)")
def test_pgvector_coincide_con_el_oraculo(corpus_y_vectores):
    chunks, vectores, emb = corpus_y_vectores
    ni = NumpyIndex()
    ni.construir(chunks, vectores, "semana", emb.firma)
    pg = PgVectorIndex(tabla="rag_chunks_test")
    pg.construir(chunks, vectores, "semana", emb.firma)
    try:
        for p in ("papa blanca", "anomalías de precio", "tomate en febrero"):
            q = emb.embed_consulta(p)
            assert_mismo_ranking(ni.buscar(q, k=8), pg.buscar(q, k=8), f"({p!r})")
        f = Filtro(slugs=frozenset({"papa-blanca"}))
        q = emb.embed_consulta("precio")
        res = pg.buscar(q, k=5, filtro=f)
        assert_mismo_ranking(ni.buscar(q, k=5, filtro=f), res, "(filtro por slug)")
        for r in res:
            assert f.acepta(r.chunk)
    finally:
        pg.cerrar()
