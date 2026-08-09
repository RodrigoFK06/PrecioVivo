"""Pruebas del constructor y publicador del índice (preciovivo.indexer).

Lo que se verifica es sobre todo la PARTICIÓN histórico/reciente y la
CUANTIZACIÓN, porque son las dos decisiones con consecuencias fuera del código:
la primera determina cuánto crece el repo cada día, y la segunda que lo que el
sitio ordena sea lo mismo que ordena el pipeline.
"""
from __future__ import annotations

import numpy as np
import pytest

from preciovivo import indexer as I
from preciovivo.corpus import TIPO_PRODUCTO_PERFIL
from preciovivo.vectorstore import NumpyIndex


@pytest.fixture
def construido(snapshot_sintetico, embedder_fake):
    idx, chunks, vectores, emb = I.construir(
        snapshot_sintetico, embedder=embedder_fake, granularidad="semana",
        indice=NumpyIndex())
    return idx, chunks, vectores, emb


# --------------------------------------------------------------------------- #
# Cuantización
# --------------------------------------------------------------------------- #
def test_ida_y_vuelta_conserva_el_vector():
    rng = np.random.default_rng(0)
    v = rng.normal(size=(50, 32)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    recuperado = I.descuantizar(I.cuantizar(v))
    # int8 con escala 127: el error por componente es <= 0.5/127.
    assert np.abs(recuperado - v).max() < 0.5 / I.ESCALA_INT8 + 1e-6


# Tolerancia del score entre el índice exacto y el cuantizado. Medido sobre el
# corpus real: error máximo 0.0046, medio 0.0016 (la cota teórica por componente
# es 0.5/127 = 0.0039 y se alcanza). 0.02 deja margen sin tapar una regresión.
TOL_CUANTIZACION = 0.02


def test_cuantizacion_conserva_la_relevancia(construido):
    """La cuantización devuelve resultados EQUIVALENTES, no idénticos.

    Y la distinción no es un tecnicismo: medido sobre el corpus real, el conjunto
    top-10 coincide solo unas 4 de cada 8 veces. En este corpus hay muchísimos
    chunks separados por menos de 0,005 de score —semanas de variedades distintas
    de papa, días de mercado casi iguales— y el redondeo a int8 basta para
    permutarlos.

    Que se permute cuál de dos casi-empatados sale quinto da igual. Lo que NO
    puede pasar es que el score del puesto i se desplome: eso sí sería entregarle
    al modelo evidencia peor. Es lo que mide este test.

    El índice del sitio es aproximado. El exacto vive en el pipeline. El README
    lo dice; esta prueba lo acota.
    """
    _, chunks, vectores, emb = construido
    exacto = NumpyIndex()
    exacto.construir(chunks, vectores, "semana", emb.firma)
    aproximado = NumpyIndex()
    aproximado.construir(chunks, I.descuantizar(I.cuantizar(vectores)),
                         "semana", emb.firma)
    for p in ("papa blanca", "tomate", "anomalía de precio", "papaya en enero"):
        q = emb.embed_consulta(p)
        a = exacto.buscar(q, k=5)
        b = aproximado.buscar(q, k=5)
        assert len(a) == len(b)
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            assert abs(x.score - y.score) < TOL_CUANTIZACION, (
                f"la cuantización degradó el puesto {i} para {p!r}: "
                f"{x.score:.5f} -> {y.score:.5f}")


def test_cuantizacion_satura_sin_desbordar():
    v = np.array([[2.0, -2.0, 0.0]], dtype=np.float32)  # fuera de [-1,1]
    q = I.cuantizar(v)
    assert q.dtype == np.int8
    assert q.max() <= 127 and q.min() >= -127


# --------------------------------------------------------------------------- #
# Partición histórico / reciente
# --------------------------------------------------------------------------- #
def test_el_corte_es_el_lunes_de_la_ultima_semana(snapshot_sintetico):
    from datetime import date

    corte = I.corte_por_defecto(snapshot_sintetico)
    assert date.fromisoformat(corte).weekday() == 0
    assert corte <= snapshot_sintetico["latestFecha"]


def test_corte_sin_fecha():
    assert I.corte_por_defecto({}) == "9999-12-31"


def test_las_fichas_nunca_son_historicas(construido):
    """Traen el último precio y el pronóstico vigente: cambian todos los días."""
    _, chunks, _, _ = construido
    perfiles = [c for c in chunks if c.tipo == TIPO_PRODUCTO_PERFIL]
    assert perfiles
    assert not any(I.es_historico(c, "2099-01-01") for c in perfiles)


def test_particion_cubre_todo_sin_solaparse(construido, snapshot_sintetico):
    _, chunks, _, _ = construido
    corte = I.corte_por_defecto(snapshot_sintetico)
    hist = [c.id for c in chunks if I.es_historico(c, corte)]
    rec = [c.id for c in chunks if not I.es_historico(c, corte)]
    assert set(hist) & set(rec) == set()
    assert len(hist) + len(rec) == len(chunks)


def test_la_parte_historica_es_la_mayoria(construido, snapshot_sintetico):
    """El punto entero de partir: la cola diaria tiene que ser pequeña."""
    _, chunks, _, _ = construido
    corte = I.corte_por_defecto(snapshot_sintetico)
    rec = [c for c in chunks if not I.es_historico(c, corte)]
    assert len(rec) < len(chunks) / 2


# --------------------------------------------------------------------------- #
# Artefacto estático
# --------------------------------------------------------------------------- #
def test_exportar_y_cargar_ida_y_vuelta(tmp_path, construido, snapshot_sintetico):
    idx, chunks, vectores, emb = construido
    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico, destino=tmp_path,
                           permitir_fake=True)

    leidos, vecs, metas = I.cargar_estatico(tmp_path)
    assert len(leidos) == len(chunks)
    assert vecs.shape == vectores.shape
    assert {c.id for c in leidos} == {c.id for c in chunks}
    # El texto es lo que llega al modelo: tiene que sobrevivir intacto.
    por_id = {c.id: c.texto for c in chunks}
    assert all(c.texto == por_id[c.id] for c in leidos)
    assert all(m["firma_embedder"] == emb.firma for m in metas.values())


def test_el_artefacto_preserva_la_relevancia(tmp_path, construido, snapshot_sintetico):
    """Lo que hay que garantizar: el sitio recupera evidencia equivalente.

    Misma salvedad que en `test_cuantizacion_conserva_la_relevancia`: los ids
    pueden permutarse entre casi-empates, los scores no pueden degradarse.
    """
    idx, chunks, vectores, emb = construido
    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico, destino=tmp_path,
                           permitir_fake=True)
    leidos, vecs, _ = I.cargar_estatico(tmp_path)

    desde_artefacto = NumpyIndex()
    desde_artefacto.construir(leidos, vecs, "semana", emb.firma)
    for p in ("papa blanca", "tomate esta semana", "anomalía"):
        q = emb.embed_consulta(p)
        a = idx.buscar(q, k=5)
        b = desde_artefacto.buscar(q, k=5)
        assert len(a) == len(b)
        for x, y in zip(a, b, strict=True):
            assert abs(x.score - y.score) < TOL_CUANTIZACION


def test_solo_reciente_no_toca_la_parte_historica(tmp_path, construido, snapshot_sintetico):
    """La razón de existir del modo: la corrida diaria no debe reescribir ~2 MB."""
    idx, chunks, vectores, _ = construido
    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico, destino=tmp_path,
                           permitir_fake=True)
    hist = tmp_path / f"{I.NOMBRE_HISTORICO}.bin"
    antes = hist.stat().st_mtime_ns, hist.read_bytes()

    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico,
                        destino=tmp_path, solo_reciente=True,
                        permitir_fake=True)
    assert hist.stat().st_mtime_ns == antes[0]
    assert hist.read_bytes() == antes[1]


def test_la_cola_diaria_es_mucho_mas_chica(tmp_path, construido, snapshot_sintetico):
    idx, chunks, vectores, _ = construido
    info = I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico,
                               destino=tmp_path, permitir_fake=True)
    assert info["bytes_reciente"] < info["bytes_historico"]


def test_se_niega_a_publicar_un_indice_de_juguete(tmp_path, construido,
                                                  snapshot_sintetico):
    """El guard de firma del sitio ya rechazaría un índice fake —falla seguro—
    pero en silencio: /api/consulta degradaría a catálogo-en-contexto y nadie se
    enteraría de que en producción nunca hubo RAG. Se rompe acá, donde se ve."""
    idx, chunks, vectores, _ = construido
    assert idx.meta.firma_embedder.startswith("fake:")
    with pytest.raises(RuntimeError, match="juguete"):
        I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico,
                            destino=tmp_path)
    assert not list(tmp_path.iterdir()), "no debió escribir nada"


def test_cargar_sin_artefacto(tmp_path):
    chunks, vecs, metas = I.cargar_estatico(tmp_path)
    assert chunks == [] and metas == {}


def test_corte_explicito(tmp_path, construido, snapshot_sintetico):
    idx, chunks, vectores, _ = construido
    todo_reciente = I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico,
                                        destino=tmp_path, corte="2000-01-01",
                                        permitir_fake=True)
    assert todo_reciente["n_historico"] == 0
    assert todo_reciente["n_reciente"] == len(chunks)


# --------------------------------------------------------------------------- #
# Carga desde el artefacto en el recuperador
# --------------------------------------------------------------------------- #
def test_recuperador_desde_artefacto(tmp_path, construido, snapshot_sintetico,
                                     embedder_fake):
    from preciovivo.retrieval import Recuperador

    idx, chunks, vectores, _ = construido
    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico, destino=tmp_path,
                           permitir_fake=True)

    rec = Recuperador.desde_artefacto(snapshot_sintetico, destino=str(tmp_path),
                                      embedder=embedder_fake)
    assert len(rec.chunks) == len(chunks)
    ctx = rec.recuperar("¿cuánto está el tomate?", k=5)
    assert ctx.piso and all(c.slug == "tomate" for c in ctx.piso)


def test_artefacto_con_otro_embebedor_falla(tmp_path, construido, snapshot_sintetico):
    """El modo de falla silencioso: consultar un índice con otro embebedor
    compara espacios distintos y devuelve ruido con total confianza."""
    from preciovivo.embeddings import FakeEmbedder
    from preciovivo.retrieval import Recuperador

    idx, chunks, vectores, _ = construido
    I.exportar_estatico(chunks, vectores, idx.meta, snapshot_sintetico, destino=tmp_path,
                           permitir_fake=True)

    otro = FakeEmbedder(dims=128)  # dims distintas => firma distinta
    with pytest.raises(RuntimeError, match="espacios vectoriales"):
        Recuperador.desde_artefacto(snapshot_sintetico, destino=str(tmp_path),
                                    embedder=otro)


def test_sin_indice_publicado_el_error_dice_como_arreglarlo(tmp_path,
                                                            snapshot_sintetico,
                                                            embedder_fake):
    from preciovivo.retrieval import Recuperador

    with pytest.raises(RuntimeError, match="--index"):
        Recuperador.desde_artefacto(snapshot_sintetico, destino=str(tmp_path),
                                    embedder=embedder_fake)
