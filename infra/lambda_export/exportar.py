"""Construye el snapshot y lo publica donde la Fase 1 ya lo está sirviendo.

DÓNDE ESCRIBE, QUE ES EL PUNTO
------------------------------
En `snapshot.json` del bucket de la Fase 1 — el MISMO objeto que la Lambda de
consultas lee al arrancar. No hay un segundo snapshot ni un formato paralelo:
la Fase 3 alimenta a la Fase 1 y dejan de ser dos demos para ser un sistema.

Consecuencia directa del diseño de la Fase 1, que conviene tener presente: los
contenedores vivos siguen sirviendo el snapshot que cargaron. La foto nueva entra
cuando AWS los recicle. Es la misma decisión de siempre — precios de cierre, un
desfase de minutos no cambia ninguna respuesta — y está argumentada en
`docs/aws.md`.

POR QUÉ ESTA LAMBDA NO PESA 174 MB
-----------------------------------
`export._add_forecast` llama a `forecast_all_cached`, así que importa `forecast`,
que importa numpy. Pero sklearn NO: `forecast` lo importa dentro de `_nuevo_gbm`,
no en el módulo. Con el caché ya escrito por el paso anterior, ese camino nunca
se recorre y el paquete se queda en numpy+holidays: 54,9 MB en vez de 173,6.

Un import perezoso escrito por otra razón acaba decidiendo el empaquetado. Por
eso se mide en vez de suponer.

EL CACHÉ NO ES OPCIONAL AQUÍ
----------------------------
`forecast_all_cached` degrada con elegancia: si no encuentra el caché, RECALCULA.
En local eso son 31 minutos; en una Lambda es un timeout garantizado — y encima
fallaría por dentro, dejando un snapshot sin pronósticos en lugar de un error.
Así que si el caché no está, esto se para antes de empezar y lo dice.
"""
from __future__ import annotations

import json
import os
import time

import boto3

import estado_s3

os.environ.setdefault("PRECIOVIVO_DB", estado_s3.RUTA_DB)

from preciovivo import export as E  # noqa: E402

BUCKET_SNAPSHOT = os.environ["BUCKET_SNAPSHOT"]
CLAVE_SNAPSHOT = os.environ.get("CLAVE_SNAPSHOT", "snapshot.json")

_s3 = boto3.client("s3")


def handler(_event, _context):
    t0 = time.perf_counter()

    if estado_s3.bajar_db() is None:
        raise RuntimeError(
            f"no hay BD en s3://{estado_s3.BUCKET}/{estado_s3.CLAVE_DB}: "
            "la ingesta no ha corrido nunca")

    if not estado_s3.bajar_cache_forecast():
        # Fallo explícito y no degradación silenciosa: ver el docstring.
        raise RuntimeError(
            f"no hay caché de forecast en s3://{estado_s3.BUCKET}/"
            f"{estado_s3.CLAVE_CACHE}. Sin él, export recalcularía el GBM entero "
            "(~31 min) dentro de una Lambda de 15. Corre antes el paso de forecast.")

    datos = E.build(estado_s3.RUTA_DB)
    cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _s3.put_object(Bucket=BUCKET_SNAPSHOT, Key=CLAVE_SNAPSHOT, Body=cuerpo,
                   ContentType="application/json; charset=utf-8")

    meta = datos.get("forecastMeta", {})
    resultado = {
        "bytes": len(cuerpo),
        "productos": datos.get("productCount"),
        "fechas": len(datos.get("fechas", [])),
        "ultima_fecha": datos.get("latestFecha"),
        # Si el forecast falló por dentro, export lo deja aquí en vez de caerse.
        # Se sube al resultado para que no se pierda en un log que nadie mira.
        "error_forecast": meta.get("error"),
        "productos_con_forecast": sum(
            1 for p in datos.get("productos", []) if p.get("forecast")),
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False, default=str))
    return resultado
