"""Construcción y publicación del índice RAG de Precio Vivo.

DOS DESTINOS, UN SOLO CORPUS
----------------------------
1. Índice local (numpy o sqlite-vec) que usan la CLI y las evaluaciones.
2. Artefacto ESTÁTICO que viaja en el deploy de Vercel y lee `web/lib/rag.ts`.

El sitio es estático + snapshot.json; meterle una base de datos en el camino de
cada pregunta sería cambiar su arquitectura por una capacidad que cabe en 3 MB.
El costo honesto de esa decisión: en producción pgvector no participa — vive en
el pipeline y en el docker-compose. El README lo dice tal cual.

PARTICIÓN HISTÓRICO / RECIENTE
------------------------------
El corpus histórico es INMUTABLE: la semana del 3 al 7 de agosto de 2026 no va a
cambiar nunca. Solo se mueven la ventana en curso, el día más reciente, las
anomalías nuevas y las fichas (que traen el último precio y el pronóstico).

Sin aprovechar eso, el índice completo se reescribiría cada día hábil. Git
guarda cada blob binario entero, así que serían ~2,3 MB diarios: unos 550 MB al
año, sobre un repo cuyo DEPLOY.md ya marca el tamaño como preocupación.

Partido, la parte histórica se commitea una vez y se refresca de vez en cuando;
la cola diaria pesa decenas de KB.

CUANTIZACIÓN
------------
Los vectores del artefacto van en int8. Como `embeddings.py` garantiza norma 1,
cada componente cae en [-1, 1] y escalar por 127 usa el rango entero completo.
El error por componente es ~0,4%, que para ORDENAR es irrelevante — pero es una
aproximación, y por eso el índice del sitio no es bit-a-bit el mismo que el
exacto de Python. Se dice, no se esconde.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .corpus import GRANULARIDAD_DEFAULT, Chunk, build_corpus, cargar_snapshot
from .embeddings import Embedder, get_embedder
from .vectorstore import IndiceMeta, IndiceVectorial, get_indice, huella_corpus

# Escala de cuantización: los vectores son unitarios, así que [-1,1] -> [-127,127].
ESCALA_INT8 = 127.0

DESTINO_WEB = os.environ.get("PRECIOVIVO_RAG_WEB", "../web/data")
NOMBRE_HISTORICO = "rag-historico"
NOMBRE_RECIENTE = "rag-reciente"


# --------------------------------------------------------------------------- #
# Caché de embeddings
# --------------------------------------------------------------------------- #
RUTA_CACHE = os.environ.get("PRECIOVIVO_EMBED_CACHE", "../data/embed_cache.npz")


def ruta_para_firma(ruta: str | Path, firma: str) -> Path:
    """Un archivo de caché POR EMBEBEDOR: `embed_cache.api_jina-...-v3_256.npz`.

    Con un único archivo compartido, el último que escribía se llevaba por
    delante lo del anterior: `leer_cache` descarta lo que no lleva su firma
    —correcto, son espacios vectoriales distintos— pero `guardar_cache`
    sobrescribía igual.

    No es teórico. Al conectar `Recuperador.desde_snapshot` a esta caché, la
    suite de tests —que usa `FakeEmbedder`— borró de un golpe los 9.206
    embeddings recién calculados con el proveedor real. La siguiente corrida
    tuvo que rehacerlos.

    Separar por firma lo hace imposible por construcción, en vez de por
    convención de "no correr los tests después del backfill".
    """
    ruta = Path(ruta)
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", firma).strip("_")
    return ruta.with_name(f"{ruta.stem}.{seguro}{ruta.suffix}")
# Cada cuántos lotes se persiste. UNO a propósito: el costo de guardar es un
# `np.savez` de pocos MB, y el de no guardar es rehacer llamadas que ya se
# pagaron —en tiempo de cuota, que es el recurso escaso—. Con un free tier
# limitado a ~100 embeddings/minuto, un backfill de 9.200 va por tandas y cada
# lote perdido son 60 segundos de cuota tirados.
LOTES_POR_CHECKPOINT = 1


def _clave(c: Chunk) -> str:
    """Identidad del embedding: el chunk Y su texto.

    Incluir el hash del texto es lo que hace correcta la caché. Los ids son
    estables por diseño (`producto-periodo:papa-blanca:2026-W32`), así que un
    chunk cuyo contenido cambia —la semana en curso recibe un día más— conserva
    su id. Cachear solo por id serviría el vector viejo para un texto nuevo, en
    silencio y para siempre.
    """
    import hashlib

    h = hashlib.sha256(c.texto.encode("utf-8")).hexdigest()[:16]
    return f"{c.id}|{h}"


def leer_cache(ruta: str | Path, firma: str) -> dict[str, np.ndarray]:
    """Vectores ya calculados para ESTA firma de embebedor. {} si no aplica.

    La firma es parte de la validez: vectores de otro modelo viven en otro
    espacio, así que un cambio de proveedor invalida la caché entera en vez de
    mezclar espacios.
    """
    ruta = ruta_para_firma(ruta, firma)
    if not ruta.exists():
        return {}
    try:
        with np.load(ruta, allow_pickle=False) as z:
            # La firma va también DENTRO del archivo, no solo en su nombre: el
            # nombre es una conveniencia, esto es la verificación.
            if str(z["firma"]) != firma:
                return {}
            claves = [str(k) for k in z["claves"]]
            vectores = z["vectores"]
    except (OSError, ValueError, KeyError):
        return {}  # caché corrupta: se descarta, no se propaga el problema
    if len(claves) != len(vectores):
        return {}
    return dict(zip(claves, vectores, strict=True))


def guardar_cache(ruta: str | Path, firma: str, mapa: dict[str, np.ndarray]) -> None:
    """Persiste la caché de forma ATÓMICA (tmp + replace).

    Sin el reemplazo atómico, una interrupción a mitad de escritura dejaría un
    .npz truncado: justo el escenario para el que existe la caché.
    """
    ruta = ruta_para_firma(ruta, firma)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # El temporal TIENE que terminar en .npz: `np.savez` le añade la extensión
    # cuando no la lleva, así que un `foo.npz.tmp` se escribía en realidad como
    # `foo.npz.tmp.npz` y el `os.replace` siguiente fallaba con FileNotFoundError
    # — que este mismo `except OSError` se tragaba. La caché nunca se guardaba y
    # el único síntoma era que todo seguía funcionando, más lento.
    tmp = ruta.with_name(ruta.name + ".tmp.npz")
    claves = sorted(mapa)
    try:
        np.savez(tmp, firma=np.array(firma), claves=np.array(claves),
                 vectores=np.stack([mapa[k] for k in claves]) if claves
                 else np.zeros((0, 0), dtype=np.float32))
        os.replace(tmp, ruta)
    except OSError:
        pass  # no poder cachear no debe tumbar la construcción del índice


def embed_con_cache(chunks: list[Chunk], emb: Embedder,
                    ruta: str | Path = RUTA_CACHE) -> np.ndarray:
    """Embebe los chunks reusando lo ya calculado y guardando sobre la marcha.

    POR QUÉ EXISTE
    --------------
    El backfill completo son ~9.200 llamadas. Con el free tier de un proveedor
    (Gemini: 100 embeddings/minuto) eso es más de hora y media en un solo
    proceso, sin puntos de retorno: una interrupción a los 80 minutos costaba
    los 80 minutos. Ya ocurrió una vez.

    Además hace barata la operación normal: el corpus histórico es inmutable, así
    que tras el primer backfill una reconstrucción COMPLETA solo embebe lo que
    cambió —la ventana en curso, el último día, las fichas— en vez de todo.
    """
    from .embeddings import EMBED_LOTE

    cache = leer_cache(ruta, emb.firma)
    claves = [_clave(c) for c in chunks]
    faltan = [i for i, k in enumerate(claves) if k not in cache]

    if faltan:
        print(f"index: {len(chunks) - len(faltan):,} embeddings en caché · "
              f"{len(faltan):,} por calcular", flush=True)
        import time

        lotes = [faltan[i:i + EMBED_LOTE] for i in range(0, len(faltan), EMBED_LOTE)]
        # El progreso se anuncia DESDE AQUÍ y no desde `embed_documentos`: quien
        # lotifica es esta capa, así que allí abajo cada llamada ve un solo lote
        # y su contador —que solo se activa con varios— quedaba mudo. Un backfill
        # de 20 minutos sin una línea de salida es indistinguible de uno colgado.
        t0 = time.monotonic()
        hechos = 0
        for n, lote in enumerate(lotes, start=1):
            vectores = emb.embed_documentos([chunks[i].texto for i in lote])
            for i, v in zip(lote, vectores, strict=True):
                cache[claves[i]] = v
            if n % LOTES_POR_CHECKPOINT == 0 or n == len(lotes):
                guardar_cache(ruta, emb.firma, cache)
            hechos += len(lote)
            restante = (time.monotonic() - t0) / hechos * (len(faltan) - hechos)
            print(f"  embeddings: {hechos:>6,}/{len(faltan):,} "
                  f"({100 * hechos / len(faltan):5.1f}%) · lote {n}/{len(lotes)} · "
                  f"faltan ~{restante / 60:.0f} min", flush=True)
    else:
        print(f"index: los {len(chunks):,} embeddings estaban en caché", flush=True)

    return np.stack([cache[k] for k in claves]).astype(np.float32)


def corte_por_defecto(snapshot: dict) -> str:
    """Frontera histórico/reciente: el lunes de la semana del último dato.

    Todo lo anterior está cerrado y no volverá a cambiar. La semana en curso sí
    puede recibir más días, así que va en la parte reciente.
    """
    ultima = snapshot.get("latestFecha")
    if not ultima:
        return "9999-12-31"
    d = date.fromisoformat(ultima)
    return (d - timedelta(days=d.weekday())).isoformat()


def es_historico(c: Chunk, corte: str) -> bool:
    """Un chunk es histórico si su ventana cerró antes del corte.

    Las fichas de producto NUNCA son históricas: traen el último precio y el
    pronóstico vigente, así que cambian todos los días.
    """
    from .corpus import TIPO_PRODUCTO_PERFIL

    if c.tipo == TIPO_PRODUCTO_PERFIL:
        return False
    return bool(c.fecha_fin) and c.fecha_fin < corte


def cuantizar(v: np.ndarray) -> np.ndarray:
    """float32 unitario -> int8. Ver la nota de CUANTIZACIÓN del módulo."""
    return np.clip(np.rint(v * ESCALA_INT8), -127, 127).astype(np.int8)


def descuantizar(q: np.ndarray) -> np.ndarray:
    return q.astype(np.float32) / ESCALA_INT8


# --------------------------------------------------------------------------- #
# Construcción
# --------------------------------------------------------------------------- #
def construir(
    origen, embedder: Embedder | None = None,
    granularidad: str = GRANULARIDAD_DEFAULT,
    indice: IndiceVectorial | None = None,
) -> tuple[IndiceVectorial, list[Chunk], np.ndarray, Embedder]:
    """Arma el corpus, lo embebe (con caché) y puebla el índice pedido."""
    snap = cargar_snapshot(origen)
    chunks = build_corpus(snap, granularidad=granularidad)
    emb = embedder or get_embedder()
    vectores = embed_con_cache(chunks, emb)
    idx = indice or get_indice()
    idx.construir(chunks, vectores, granularidad, emb.firma)
    return idx, chunks, vectores, emb


# --------------------------------------------------------------------------- #
# Artefacto estático para el sitio
# --------------------------------------------------------------------------- #
def _escribir_parte(
    destino: Path, nombre: str, chunks: list[Chunk], vectores: np.ndarray,
    meta: dict,
) -> tuple[int, int]:
    """Escribe `<nombre>.bin` (int8) y `<nombre>.json.gz` (chunks + meta).

    El JSON va comprimido porque el texto de los chunks es muy repetitivo —
    mismas frases con distintos números— y gzip lo reduce ~8x. Node lo
    descomprime con zlib, que es de la librería estándar: cero dependencias
    nuevas en el sitio.
    """
    destino.mkdir(parents=True, exist_ok=True)
    ruta_bin = destino / f"{nombre}.bin"
    ruta_json = destino / f"{nombre}.json.gz"

    q = cuantizar(vectores) if len(chunks) else np.zeros((0, meta["dims"]), dtype=np.int8)
    ruta_bin.write_bytes(q.tobytes())

    payload = {
        "meta": meta,
        "chunks": [
            # Se omiten campos redundantes para el sitio: `mercado` es constante
            # y `producto` se deriva del catálogo del snapshot.
            {"id": c.id, "tipo": c.tipo, "texto": c.texto, "slug": c.slug,
             "d0": c.fecha_inicio, "d1": c.fecha_fin}
            for c in chunks
        ],
    }
    with gzip.open(ruta_json, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return ruta_bin.stat().st_size, ruta_json.stat().st_size


def exportar_estatico(
    chunks: list[Chunk], vectores: np.ndarray, meta: IndiceMeta,
    snapshot: dict, destino: str | Path = DESTINO_WEB,
    corte: str | None = None, solo_reciente: bool = False,
    permitir_fake: bool = False, permitir_local: bool = False,
) -> dict:
    """Escribe el artefacto de dos partes que consume el sitio.

    `solo_reciente` evita reescribir la parte histórica en la corrida diaria: es
    justo lo que hace que el repo no engorde 2 MB por día.

    Se NIEGA a publicar un índice construido con FakeEmbedder. El guard de firma
    del sitio ya lo rechazaría —así que falla seguro— pero el modo de falla es
    silencioso: /api/consulta degradaría a catálogo-en-contexto y nadie se
    enteraría de que en producción nunca hubo RAG. Mejor romper acá, donde se ve.
    """
    destino = Path(destino)
    if meta.firma_embedder.startswith("fake:") and not permitir_fake:
        raise RuntimeError(
            f"Negado: el índice se construyó con '{meta.firma_embedder}', un "
            f"embebedor de juguete para tests. Publicarlo dejaría al sitio "
            f"degradando en silencio a catálogo-en-contexto.\n"
            f"Define EMBED_API_KEY (o PRECIOVIVO_EMBED_PROVIDER=local) y "
            f"reconstruye. Para inspeccionarlo localmente sin publicar: "
            f"ingest --index --no-publicar.")
    if meta.firma_embedder.startswith("local:") and not permitir_local:
        # ANTES ESTO ERA UN AVISO, Y NO ALCANZÓ. Se publicó igual un índice
        # `local:` y el sitio quedó meses sin recuperación vectorial: `rag.ts`
        # embebe la consulta con `api:...` vía HTTP —Vercel no puede correr
        # model2vec— así que la firma nunca coincide, `recuperar()` cae al
        # `catch`, y como el piso determinista sí devuelve chunks la respuesta
        # se sirve igual y se etiqueta `llm-rag`. Falla en silencio y hacia
        # arriba: parece que funciona.
        #
        # Un aviso que se puede ignorar no es un control. Ahora aborta, y para
        # hacerlo a propósito hay un flag explícito y auditable, igual que
        # PRECIOVIVO_API_ABIERTA en la API.
        raise RuntimeError(
            f"Negado: el índice se construyó con '{meta.firma_embedder}' y "
            f"publicarlo dejaría al sitio SIN recuperación vectorial, en "
            f"silencio: Vercel no puede embeber la consulta con un modelo local, "
            f"así que la firma jamás coincidiría.\n"
            f"Para producción: define EMBED_API_KEY y reconstruye.\n"
            f"Para inspeccionar el índice local sin publicarlo: "
            f"ingest --index --no-publicar.\n"
            f"Si de verdad quieres escribir este artefacto (p. ej. un ensayo a "
            f"un directorio temporal): ingest --index --permitir-embedder-local.")
    corte = corte or corte_por_defecto(snapshot)

    idx_hist = [i for i, c in enumerate(chunks) if es_historico(c, corte)]
    idx_rec = [i for i, c in enumerate(chunks) if not es_historico(c, corte)]

    base = asdict(meta) | {"corte": corte, "escala_int8": ESCALA_INT8}
    info: dict = {"corte": corte, "n_historico": len(idx_hist),
                  "n_reciente": len(idx_rec)}

    if not solo_reciente:
        b, j = _escribir_parte(
            destino, NOMBRE_HISTORICO, [chunks[i] for i in idx_hist],
            vectores[idx_hist] if idx_hist else vectores[:0],
            base | {"parte": "historico", "n_chunks": len(idx_hist),
                    "huella_corpus": huella_corpus([chunks[i] for i in idx_hist])})
        info["bytes_historico"] = b + j

    b, j = _escribir_parte(
        destino, NOMBRE_RECIENTE, [chunks[i] for i in idx_rec],
        vectores[idx_rec] if idx_rec else vectores[:0],
        base | {"parte": "reciente", "n_chunks": len(idx_rec),
                "huella_corpus": huella_corpus([chunks[i] for i in idx_rec])})
    info["bytes_reciente"] = b + j
    return info


def cargar_estatico(destino: str | Path = DESTINO_WEB
                    ) -> tuple[list[Chunk], np.ndarray, dict]:
    """Lee el artefacto de vuelta en Python. Sirve para verificar que el sitio
    ve lo mismo que el pipeline —y los tests lo usan para eso."""
    destino = Path(destino)
    chunks: list[Chunk] = []
    trozos: list[np.ndarray] = []
    metas: dict[str, dict] = {}
    for nombre in (NOMBRE_HISTORICO, NOMBRE_RECIENTE):
        rj, rb = destino / f"{nombre}.json.gz", destino / f"{nombre}.bin"
        if not rj.exists() or not rb.exists():
            continue
        with gzip.open(rj, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        meta = payload["meta"]
        metas[nombre] = meta
        chunks.extend(
            Chunk(id=c["id"], tipo=c["tipo"], texto=c["texto"], slug=c["slug"],
                  producto=None, mercado="", fecha_inicio=c["d0"], fecha_fin=c["d1"])
            for c in payload["chunks"])
        crudo = np.frombuffer(rb.read_bytes(), dtype=np.int8)
        if crudo.size:
            trozos.append(descuantizar(crudo.reshape(-1, meta["dims"])))
    vectores = (np.vstack(trozos) if trozos
                else np.zeros((0, 0), dtype=np.float32))
    return chunks, vectores, metas


# --------------------------------------------------------------------------- #
# Entrada desde `ingest --index`
# --------------------------------------------------------------------------- #
def main_index(origen, granularidad: str = GRANULARIDAD_DEFAULT,
               backend: str | None = None, publicar: bool = True,
               solo_reciente: bool = False, destino: str | Path = DESTINO_WEB,
               permitir_fake: bool = False, permitir_local: bool = False) -> int:
    snap = cargar_snapshot(origen)
    emb = get_embedder()
    print(f"index: embebedor={emb.firma} granularidad={granularidad}")

    idx, chunks, vectores, _ = construir(snap, embedder=emb,
                                         granularidad=granularidad,
                                         indice=get_indice(backend))
    print(f"index: {len(chunks)} chunks · {vectores.shape[1]} dims · "
          f"backend={type(idx).__name__} · huella={idx.meta.huella_corpus}")

    if publicar:
        info = exportar_estatico(chunks, vectores, idx.meta, snap,
                                 destino=destino, solo_reciente=solo_reciente,
                                 permitir_fake=permitir_fake,
                                 permitir_local=permitir_local)
        kb = lambda n: f"{n / 1024:.0f} KB"  # noqa: E731
        hist = (kb(info["bytes_historico"]) if "bytes_historico" in info
                else "sin reescribir")
        print(f"index: publicado corte={info['corte']} · "
              f"historico {info['n_historico']} chunks ({hist}) · "
              f"reciente {info['n_reciente']} chunks ({kb(info['bytes_reciente'])})")
    idx.cerrar()
    return 0
