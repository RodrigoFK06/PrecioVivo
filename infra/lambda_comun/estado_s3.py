"""El estado del pipeline en S3, con bloqueo optimista.

QUÉ ES EL ESTADO
----------------
    estado/preciovivo.db        la SQLite entera (7,9 MB)
    estado/forecast_cache.json  lo que calcula el forecast y consume el export
    estado/sisap_check.json     el contraste con SISAP que el export adjunta

POR QUÉ S3 Y NO UNA BASE GESTIONADA
------------------------------------
Hay UN escritor al día. Una RDS costaría ~15 USD/mes encendida a todas horas
para servir esa escritura, y arrastraría VPC, y la VPC arrastraría NAT Gateway o
endpoints de VPC. Bajar 7,9 MB, mutarlos y volver a subirlos es tosco y es lo
correcto a esta escala. Deja de serlo el día que haya escritores concurrentes de
verdad; hoy no los hay.

POR QUÉ CONDICIONAL Y NO UN PutObject A SECAS
----------------------------------------------
EventBridge Scheduler entrega AL MENOS UNA VEZ: es el contrato del servicio, no
una hipótesis. Dos ejecuciones solapadas descargarían la misma SQLite, cada una
haría su trabajo, y la última en subir borraría el trabajo de la otra sin dejar
un solo error en ningún log. La pérdida silenciosa es el peor fallo posible
porque nadie la busca.

`IfMatch` con el ETag que se leyó convierte eso en un 412: la segunda ejecución
falla ruidosamente y la máquina de estados lo marca en rojo. Cuesta cero.
"""
from __future__ import annotations

import os

import boto3

BUCKET = os.environ["BUCKET_ESTADO"]
CLAVE_DB = os.environ.get("CLAVE_DB", "estado/preciovivo.db")
CLAVE_CACHE = os.environ.get("CLAVE_CACHE_FORECAST", "estado/forecast_cache.json")
CLAVE_SISAP = os.environ.get("CLAVE_SISAP", "estado/sisap_check.json")

# La SQLite y el caché del forecast tienen que caer en el MISMO directorio:
# forecast._cache_path() deriva la ruta del caché de la de la BD. Si se separan,
# `export` no encuentra el caché y recomputa el GBM entero dentro de una Lambda.
DIR = "/tmp"
RUTA_DB = os.path.join(DIR, "preciovivo.db")
RUTA_CACHE = os.path.join(DIR, "forecast_cache.json")
# sisap._check_path() lo deriva igual: mismo directorio que la BD.
RUTA_SISAP = os.path.join(DIR, "sisap_check.json")

_s3 = boto3.client("s3")


def bajar_db() -> str | None:
    """Trae la SQLite. Devuelve su ETag, o None si todavía no existe.

    None es la primera ejecución, no un fallo: se distingue por el código de la
    excepción y no por un try/except mudo que se tragaría un problema de red.
    """
    try:
        r = _s3.get_object(Bucket=BUCKET, Key=CLAVE_DB)
    except _s3.exceptions.NoSuchKey:
        return None
    with open(RUTA_DB, "wb") as f:
        f.write(r["Body"].read())
    return r["ETag"]


def subir_db(etag: str | None) -> None:
    """Sube la SQLite solo si nadie la tocó mientras trabajábamos.

    Con etag -> IfMatch: 412 si cambió.
    Sin etag (primera vez) -> IfNoneMatch "*": 412 si otro la creó antes.
    """
    with open(RUTA_DB, "rb") as f:
        cuerpo = f.read()
    condicion = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
    _s3.put_object(Bucket=BUCKET, Key=CLAVE_DB, Body=cuerpo, **condicion)


def bajar_cache_forecast() -> bool:
    """Trae forecast_cache.json si existe. False si no, que es degradación válida."""
    try:
        r = _s3.get_object(Bucket=BUCKET, Key=CLAVE_CACHE)
    except _s3.exceptions.NoSuchKey:
        return False
    with open(RUTA_CACHE, "wb") as f:
        f.write(r["Body"].read())
    return True


def subir_cache_forecast(cuerpo: bytes) -> None:
    _s3.put_object(Bucket=BUCKET, Key=CLAVE_CACHE, Body=cuerpo,
                   ContentType="application/json")


def bajar_sisap() -> bool:
    """Trae sisap_check.json si existe. False si no, que es degradación válida:
    el bloque `verificacion` simplemente no aparece en el snapshot."""
    try:
        r = _s3.get_object(Bucket=BUCKET, Key=CLAVE_SISAP)
    except _s3.exceptions.NoSuchKey:
        return False
    with open(RUTA_SISAP, "wb") as f:
        f.write(r["Body"].read())
    return True


def subir_sisap() -> None:
    with open(RUTA_SISAP, "rb") as f:
        _s3.put_object(Bucket=BUCKET, Key=CLAVE_SISAP, Body=f.read(),
                       ContentType="application/json")
