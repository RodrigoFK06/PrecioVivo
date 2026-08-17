"""Pruebas del constructor y publicador del índice (preciovivo.indexer).

Lo que se verifica es sobre todo la PARTICIÓN histórico/reciente y la
CUANTIZACIÓN, porque son las dos decisiones con consecuencias fuera del código:
la primera determina cuánto crece el repo cada día, y la segunda que lo que el
sitio ordena sea lo mismo que ordena el pipeline.
"""
from __future__ import annotations

from dataclasses import replace

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


def test_se_niega_a_publicar_un_indice_local(tmp_path, construido, snapshot_sintetico):
    """Publicar un índice `local:` apaga la recuperación vectorial EN SILENCIO.

    `web/lib/rag.ts` embebe la consulta con `api:<modelo>:<dims>` por HTTP —Vercel
    no puede correr model2vec—, así que la firma nunca coincide; `recuperar()`
    captura el error y devuelve solo el piso determinista, y como el piso trae
    chunks la respuesta se sirve igual etiquetada 'llm-rag'.

    Esto era un `print` de aviso y se publicó igual: el índice del repo llevaba
    firma `local:` y el sitio estuvo sin búsqueda vectorial. Un aviso que se
    puede ignorar no es un control; ahora aborta.
    """
    idx, chunks, vectores, _ = construido
    meta_local = replace(idx.meta, firma_embedder="local:minishlab/potion:256")

    with pytest.raises(RuntimeError, match="SIN recuperación vectorial"):
        I.exportar_estatico(chunks, vectores, meta_local, snapshot_sintetico,
                            destino=tmp_path)
    assert not list(tmp_path.iterdir()), "no debió escribir nada"


def test_el_bloqueo_local_tiene_una_salida_explicita(tmp_path, construido,
                                                     snapshot_sintetico):
    """Se puede publicar un índice local a propósito, pero hay que pedirlo.

    Mismo patrón que PRECIOVIVO_API_ABIERTA: el default falla cerrado y abrirlo
    es un acto explícito y auditable, no un efecto secundario.
    """
    idx, chunks, vectores, _ = construido
    meta_local = replace(idx.meta, firma_embedder="local:minishlab/potion:256")

    info = I.exportar_estatico(chunks, vectores, meta_local, snapshot_sintetico,
                               destino=tmp_path, permitir_local=True)
    assert info["n_historico"] + info["n_reciente"] == len(chunks)
    assert (tmp_path / "rag-reciente.bin").exists()


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


# --------------------------------------------------------------------------- #
# Caché de embeddings
# --------------------------------------------------------------------------- #
class _EmbedderContador:
    """Envuelve un embebedor y cuenta cuántos textos se embeben de verdad."""

    def __init__(self, base):
        self._base = base
        self.firma = base.firma
        self.dims = base.dims
        self.textos = 0

    def embed_documentos(self, textos):
        self.textos += len(textos)
        return self._base.embed_documentos(textos)

    def embed_consulta(self, texto):
        return self._base.embed_consulta(texto)


def _chunks_de_prueba(n=5, textos=None):
    from preciovivo.corpus import Chunk

    return [
        Chunk(id=f"producto-periodo:x:{i}", tipo="producto-periodo",
              texto=(textos[i] if textos else f"texto numero {i}"),
              slug="x", producto="X", mercado="M",
              fecha_inicio="2026-01-05", fecha_fin="2026-01-09")
        for i in range(n)
    ]


def test_cache_de_embeddings_evita_recalcular(tmp_path, embedder_fake):
    """El backfill completo son ~9.200 llamadas; con un free tier limitado por
    cuota eso es más de hora y media. Sin caché, una interrupción a los 80
    minutos costaba los 80 minutos — y ocurrió."""
    emb = _EmbedderContador(embedder_fake)
    chunks = _chunks_de_prueba()
    ruta = tmp_path / "embed_cache.npz"

    v1 = I.embed_con_cache(chunks, emb, ruta)
    assert emb.textos == len(chunks)
    # El archivo real lleva la firma en el nombre: un embebedor, una caché.
    real = I.ruta_para_firma(ruta, emb.firma)
    assert real.exists() and real.stat().st_size > 0, "la caché debe persistirse"

    v2 = I.embed_con_cache(chunks, emb, ruta)
    assert emb.textos == len(chunks), "la segunda pasada no debe embeber nada"
    assert np.array_equal(v1, v2)


def test_cache_se_invalida_cuando_cambia_el_TEXTO(tmp_path, embedder_fake):
    """Los ids de chunk son estables por diseño, así que la semana en curso
    conserva su id al recibir un día más. Cachear solo por id serviría el vector
    viejo para el texto nuevo, en silencio y para siempre."""
    emb = _EmbedderContador(embedder_fake)
    ruta = tmp_path / "embed_cache.npz"

    v1 = I.embed_con_cache(_chunks_de_prueba(), emb, ruta)
    base = emb.textos

    textos = [f"texto numero {i}" for i in range(5)]
    textos[2] = "MISMO id, texto distinto"
    v2 = I.embed_con_cache(_chunks_de_prueba(textos=textos), emb, ruta)

    assert emb.textos - base == 1, "solo el chunk cambiado se re-embebe"
    assert not np.array_equal(v2[2], v1[2]), "el vector del chunk cambiado es nuevo"
    assert np.array_equal(v2[0], v1[0]), "los demás salen de la caché"


def test_cache_no_cruza_embebedores(tmp_path, embedder_fake):
    """Vectores de otro modelo viven en otro espacio: la firma invalida la
    caché entera en vez de mezclarlos."""
    ruta = tmp_path / "embed_cache.npz"
    I.embed_con_cache(_chunks_de_prueba(), embedder_fake, ruta)

    assert I.leer_cache(ruta, embedder_fake.firma), "misma firma: se reusa"
    assert I.leer_cache(ruta, "api:otro-modelo:256") == {}, "otra firma: se descarta"


def test_cache_corrupta_no_tumba_la_construccion(tmp_path, embedder_fake):
    ruta = tmp_path / "embed_cache.npz"
    ruta.write_bytes(b"esto no es un npz")
    v = I.embed_con_cache(_chunks_de_prueba(), embedder_fake, ruta)
    assert v.shape[0] == 5, "una caché ilegible se descarta, no se propaga"


def test_cada_embebedor_tiene_su_propio_archivo_de_cache(tmp_path, embedder_fake):
    """Un archivo por firma. Con uno compartido, el último en escribir borraba
    lo del anterior — `leer_cache` descartaba por firma (correcto) pero
    `guardar_cache` sobrescribía igual.

    No es hipotético: al conectar `desde_snapshot` a la caché, la suite de tests
    (FakeEmbedder) borró los 9.206 embeddings recién calculados con el proveedor
    real. Separar por firma lo hace imposible por construcción.
    """
    base = tmp_path / "embed_cache.npz"
    chunks = _chunks_de_prueba()

    I.embed_con_cache(chunks, embedder_fake, base)
    assert len(I.leer_cache(base, embedder_fake.firma)) == len(chunks)

    # Otro embebedor, mismo `base`: no puede pisar al primero.
    class _Otro:
        firma = "api:modelo-real:256"
        dims = 64

        def embed_documentos(self, textos):
            return embedder_fake.embed_documentos(textos)

    I.embed_con_cache(chunks, _Otro(), base)

    assert len(I.leer_cache(base, embedder_fake.firma)) == len(chunks), (
        "la caché del primer embebedor debe sobrevivir")
    assert len(I.leer_cache(base, "api:modelo-real:256")) == len(chunks)

    escritos = sorted(p.name for p in tmp_path.glob("embed_cache*.npz"))
    assert len(escritos) == 2, f"se esperaban dos archivos distintos: {escritos}"


def test_la_ruta_de_cache_es_un_nombre_de_archivo_valido():
    """La firma lleva ':' y '/', que no valen en un nombre de archivo."""
    r = I.ruta_para_firma("/tmp/embed_cache.npz", "local:minishlab/potion-x:256")
    assert ":" not in r.name and "/" not in r.name
    assert r.suffix == ".npz" and r.parent.name == "tmp"
