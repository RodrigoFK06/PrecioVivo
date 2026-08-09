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
    """Arma el corpus, lo embebe y puebla el índice pedido."""
    snap = cargar_snapshot(origen)
    chunks = build_corpus(snap, granularidad=granularidad)
    emb = embedder or get_embedder()
    vectores = emb.embed_documentos([c.texto for c in chunks])
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
    permitir_fake: bool = False,
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
    if meta.firma_embedder.startswith("local:"):
        # No se bloquea —para la CLI es exactamente el índice correcto— pero
        # Vercel no puede correr model2vec, así que este artefacto NO sirve en
        # producción: el sitio detectaría la firma distinta y degradaría. Se
        # avisa fuerte para que no se descubra después de un deploy.
        print(f"AVISO: índice construido con '{meta.firma_embedder}'. Sirve para "
              f"la CLI y las evaluaciones, pero NO para el sitio: Vercel no puede "
              f"embeber la consulta con un modelo local. Para producción hace "
              f"falta EMBED_API_KEY. No commitees web/data/rag-* con este índice.")
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
               permitir_fake: bool = False) -> int:
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
                                 permitir_fake=permitir_fake)
        kb = lambda n: f"{n / 1024:.0f} KB"  # noqa: E731
        hist = (kb(info["bytes_historico"]) if "bytes_historico" in info
                else "sin reescribir")
        print(f"index: publicado corte={info['corte']} · "
              f"historico {info['n_historico']} chunks ({hist}) · "
              f"reciente {info['n_reciente']} chunks ({kb(info['bytes_reciente'])})")
    idx.cerrar()
    return 0
