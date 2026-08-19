"""Reconstruye la ventana reciente del índice RAG y la publica en S3.

LA TRAMPA QUE ESTE ARCHIVO EXISTE PARA EVITAR
----------------------------------------------
`main_index(solo_reciente=True)` NO embebe solo lo reciente. Embebe el corpus
ENTERO y luego escribe únicamente la parte reciente. Lo que hace barata la
corrida diaria no es el flag: es el CACHÉ de embeddings, que ya tiene los ~9.000
chunks históricos y deja solo los ~160 nuevos por calcular.

En una Lambda, `/tmp` se va con el contenedor. Sin traer el caché desde S3, cada
corrida diaria embebería el corpus completo: ~1,5 M de tokens. Con el saldo
actual de la clave de Jina, eso son tres días hasta quedarse sin nada — y sin un
solo error, porque técnicamente todo "funciona".

Por eso el caché viaja a S3 y vuelve, y por eso existe el tope de abajo.

EL TOPE, que es la parte que de verdad protege
-----------------------------------------------
Antes de gastar un token se cuentan los chunks que NO están en el caché. Si son
más de los que una corrida diaria puede justificar, esto se detiene y lo dice en
vez de vaciar la cuota en silencio.

Un caché perdido y una reconstrucción legítima se ven IGUAL desde dentro: miles
de chunks por calcular. La diferencia es que una la pediste tú. Por eso el tope
se salta a mano (`TOPE_EMBEDDINGS`) y no se relaja solo.

QUÉ PUBLICA
-----------
    estado/embed_cache.<firma>.npz   el caché, para la próxima corrida
    rag/rag-reciente.bin             vectores int8 de la ventana reciente
    rag/rag-reciente.json.gz         metadatos y textos

La parte HISTÓRICA no se toca: el corpus pasado es inmutable y reescribirlo a
diario engordaría el artefacto ~2 MB por día sin cambiar una sola respuesta.
"""
from __future__ import annotations

import json
import os
import time

import boto3

RUTA_SNAPSHOT = "/tmp/snapshot.json"
DESTINO_RAG = "/tmp/rag"
# `indexer.ruta_para_firma` le añade la firma del embebedor, así que el archivo
# real es /tmp/embed_cache.<firma>.npz. Se fija ANTES de importar el paquete:
# indexer lee esta variable en tiempo de import.
os.environ.setdefault("PRECIOVIVO_EMBED_CACHE", "/tmp/embed_cache.npz")
os.environ.setdefault("PRECIOVIVO_RAG_WEB", DESTINO_RAG)

from preciovivo import indexer as IX  # noqa: E402
from preciovivo.corpus import build_corpus  # noqa: E402
from preciovivo.embeddings import get_embedder  # noqa: E402

BUCKET_SNAPSHOT = os.environ["BUCKET_SNAPSHOT"]
CLAVE_SNAPSHOT = os.environ.get("CLAVE_SNAPSHOT", "snapshot.json")
PREFIJO_RAG = os.environ.get("PREFIJO_RAG", "rag/")
PREFIJO_ESTADO = os.environ.get("PREFIJO_ESTADO", "estado/")
PARAM_CLAVE_EMBED = os.environ["PARAM_CLAVE_EMBED"]
GRANULARIDAD = os.environ.get("GRANULARIDAD", "semana")
# Una corrida diaria mueve la ventana de la semana en curso más las fichas de los
# productos que cambiaron: del orden de 160-300 chunks. 600 deja margen para un
# lunes con varios días acumulados sin llegar a parecerse a un backfill.
TOPE_EMBEDDINGS = int(os.environ.get("TOPE_EMBEDDINGS", "600"))

_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")


def _clave_api() -> str:
    """La clave de Jina, desde Parameter Store.

    En Parameter Store y no en Secrets Manager: éste cobra 0,40 USD por secreto
    al mes y aquél, en tier estándar, es gratis. Para una clave que se lee una
    vez al día, la rotación automática de Secrets Manager no compensa.

    El parámetro NO lo gestiona CDK. Un secreto en la definición de la
    infraestructura acaba en el repositorio, en el historial de CloudFormation y
    en los logs de despliegue. Se crea una vez a mano y aquí solo se lee.
    """
    r = _ssm.get_parameter(Name=PARAM_CLAVE_EMBED, WithDecryption=True)
    return r["Parameter"]["Value"]


def _ruta_cache(firma: str) -> str:
    return str(IX.ruta_para_firma(IX.RUTA_CACHE, firma))


def _clave_cache_s3(firma: str) -> str:
    """El caché vive junto al resto del estado, con la firma en el nombre.

    La firma en el nombre no es cosmética: es el guard de la Fase B. Dos
    embebedores distintos producen vectores de espacios distintos, y un caché
    compartido los mezclaría sin quejarse.
    """
    return PREFIJO_ESTADO + os.path.basename(_ruta_cache(firma))


def handler(event, _context):
    t0 = time.perf_counter()
    os.makedirs(DESTINO_RAG, exist_ok=True)

    # La clave sí se puede fijar aquí: `_embed_api_key()` es una función y se
    # evalúa al construir el embebedor.
    #
    # EMBED_MODEL, EMBED_BASE_URL y EMBED_DIMS NO: `embeddings.py` los lee como
    # constantes de módulo, en tiempo de import. Ponerlos aquí llegaba tarde y la
    # función acababa usando el modelo por defecto (text-embedding-3-small) con
    # la clave de Jina. Van en la configuración de la Lambda, que es donde se ven.
    #
    # No fue un fallo silencioso, y por eso se encontró: la firma resultante era
    # `api:text-embedding-3-small:256` y no `api:jina-embeddings-v3:256`, así que
    # el caché no coincidía y el preflight lo reportó como "0 vectores".
    os.environ["EMBED_API_KEY"] = _clave_api()

    cuerpo = _s3.get_object(Bucket=BUCKET_SNAPSHOT, Key=CLAVE_SNAPSHOT)["Body"].read()
    with open(RUTA_SNAPSHOT, "wb") as f:
        f.write(cuerpo)

    emb = get_embedder("api")
    clave_cache = _clave_cache_s3(emb.firma)
    ruta_cache = _ruta_cache(emb.firma)

    hay_cache = False
    try:
        r = _s3.get_object(Bucket=BUCKET_SNAPSHOT, Key=clave_cache)
        with open(ruta_cache, "wb") as f:
            f.write(r["Body"].read())
        hay_cache = True
    except _s3.exceptions.NoSuchKey:
        pass

    # --- Preflight: contar ANTES de gastar -------------------------------
    snap = IX.cargar_snapshot(RUTA_SNAPSHOT)
    chunks = build_corpus(snap, granularidad=GRANULARIDAD)
    cache = IX.leer_cache(IX.RUTA_CACHE, emb.firma)
    faltan = sum(1 for c in chunks if IX._clave(c) not in cache)

    tope = int((event or {}).get("tope", TOPE_EMBEDDINGS))
    if faltan > tope:
        raise RuntimeError(
            f"{faltan:,} chunks por embeber, por encima del tope de {tope:,}. "
            f"(caché en S3: {'sí' if hay_cache else 'NO'}, {len(cache):,} vectores). "
            f"Una corrida diaria mueve ~160-300; esto se parece a un backfill "
            f"completo, que costaría ~{faltan * 163 / 1e6:.2f} M tokens de la cuota "
            f"de Jina. Si es intencionado, invoca con {{\"tope\": {faltan + 1}}}.")

    n = IX.main_index(RUTA_SNAPSHOT, granularidad=GRANULARIDAD,
                      solo_reciente=True, publicar=True, destino=DESTINO_RAG)
    if n != 0:
        raise RuntimeError(f"main_index devolvió {n}")

    subidos = {}
    for nombre in ("rag-reciente.bin", "rag-reciente.json.gz"):
        ruta = os.path.join(DESTINO_RAG, nombre)
        with open(ruta, "rb") as f:
            datos = f.read()
        _s3.put_object(Bucket=BUCKET_SNAPSHOT, Key=PREFIJO_RAG + nombre, Body=datos)
        subidos[nombre] = len(datos)

    with open(ruta_cache, "rb") as f:
        _s3.put_object(Bucket=BUCKET_SNAPSHOT, Key=clave_cache, Body=f.read())

    resultado = {
        "firma_embedder": emb.firma,
        "chunks_totales": len(chunks),
        "cache_previa": len(cache),
        "embebidos_ahora": faltan,
        # El costo en tokens es el número que hay que vigilar, así que se reporta
        # en cada corrida en vez de descubrirse cuando la cuota se acabe.
        "tokens_estimados": faltan * 163,
        "cache_estaba_en_s3": hay_cache,
        "subidos": subidos,
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False))
    return resultado
