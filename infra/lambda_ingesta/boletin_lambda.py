"""El boletín multi-mercado: las frutas que el reporte 335 no cubre.

QUÉ AÑADE, Y POR QUÉ FALTABA
-----------------------------
El reporte 335 dice en su propia portada qué alcance tiene:

    BOLETÍN DIARIO
    Hortalizas · Legumbres · Tubérculos y raíces

Tres categorías. Las frutas no están, y no por un fallo del parser —comprobado
contra el PDF: 93 líneas de texto, 72 productos extraídos, no se pierde ninguno—
sino porque esa publicación no las incluye.

Viven en la colección 338, el boletín de abastecimiento GMML + Mercado Mayorista
de Frutas Nº2. Son ~74 productos más: albaricoque, camu camu, carambola,
chirimoya, ciruela. El catálogo pasa de 73 a 147.

QUÉ NO INGESTA, Y ES DELIBERADO
--------------------------------
El boletín trae TAMBIÉN los 71 productos del GMML, y esos se descartan. La serie
diaria del GMML viene del reporte 335 y mezclar dos fuentes para el mismo dato
destruiría justo lo que las hace valiosas por separado: el 335 publica el precio
de CIERRE del día, el boletín publica el PROMEDIO de la semana en curso. Son
magnitudes distintas con el mismo nombre.

`ingest._run_boletin` ya aplica esa regla; aquí no se reimplementa.

POR QUÉ ES UN PASO APARTE, COMO SISAP
--------------------------------------
Falla distinto y no puede bloquear. Si la colección 338 no publica hoy, o cambia
el layout de su grid, los precios del 335 ya están cargados y el sitio tiene que
salir igual — con 73 productos en vez de 147, pero al día.

En la máquina de estados eso es un `Catch` que sigue adelante.

Y SÍ SE ALCANZA DESDE AWS
--------------------------
A diferencia de SISAP —que vive en `sistemas.midagri.gob.pe` y no acepta
conexiones desde AWS— el boletín está en la misma infraestructura que el reporte
335: `gob.pe` y su CDN, que la sonda del WAF ya verificó alcanzables desde
Lambda. Por eso este paso sí puede vivir aquí y el de SISAP no.
"""
from __future__ import annotations

import json
import os
import time

import estado_s3

os.environ.setdefault("PRECIOVIVO_RAW", "/tmp/raw")
os.environ.setdefault("PRECIOVIVO_DB", estado_s3.RUTA_DB)

from preciovivo.ingest import _run_boletin
from preciovivo.store import Store


def handler(event, _context):
    t0 = time.perf_counter()
    os.makedirs("/tmp/raw", exist_ok=True)

    etag = estado_s3.bajar_db()
    if etag is None:
        raise RuntimeError(
            f"no hay BD en s3://{estado_s3.BUCKET}/{estado_s3.CLAVE_DB}: "
            "el boletín se ingesta sobre la base que ya trae el 335")

    store = Store()
    antes = store.count("precios_diarios")

    # Cadena vacía = «busca el del día en la colección 338». La navegación vive
    # en `harvester.Coleccion`, compartida con el reporte 335.
    _run_boletin(store, (event or {}).get("pdf", ""))

    despues = store.count("precios_diarios")
    productos = store.count("productos")
    store.close()
    estado_s3.subir_db(etag)

    resultado = {
        "filas_nuevas": despues - antes,
        "filas_precio": despues,
        "productos_totales": productos,
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False, default=str))
    return resultado
