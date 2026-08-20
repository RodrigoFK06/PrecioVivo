"""Cosecha, parseo y carga del reporte diario del GMML, en Lambda.

QUÉ HACE Y QUÉ NO
-----------------
Hace exactamente lo que `python -m preciovivo.ingest --latest N`: baja los PDFs
nuevos, los parsea y los carga. Y lo hace llamando a los módulos REALES del
pipeline (`harvester`, `parser`, `store`), instalados como paquete. No hay una
segunda implementación de la cosecha que pueda derivar de la primera.

Lo que NO hace es pronosticar ni exportar. Cada paso es su propia función porque
sus dependencias, su duración y su política de reintentos son distintas — y esa
última es la razón de peso: reintentar la red es correcto, reintentar el parseo
es tirar dinero.

EL ESTADO VIVE EN S3, NO EN UNA BASE GESTIONADA
-----------------------------------------------
La SQLite entera son 7,9 MB. Se descarga a /tmp, se muta y se vuelve a subir.
Suena tosco y es lo correcto aquí: hay UN escritor al día. Una RDS costaría
~15 USD/mes de instancia encendida a todas horas para servir una escritura
diaria, y además obligaría a VPC, y la VPC obligaría a NAT Gateway o endpoints.
El día que haya varios escritores concurrentes esto deja de valer; hoy no los hay.

La descarga, la subida y el bloqueo optimista viven en `estado_s3`, que
comparten los tres pasos. Ahí está el argumento de por qué la escritura es
condicional.

LA COMPUERTA DE ESCRITURA
-------------------------
`ingest.MIN_ROWS` bloquea la carga de un reporte estructuralmente roto. Esa parte
ya funciona. Lo que no funcionaba es lo que pasa DESPUÉS: la CLI hace `return 0`
pase lo que pase, así que un cambio de layout del PDF — justo lo que la compuerta
existe para detectar — deja la tarea diaria en verde.

Aquí se devuelve el recuento en el resultado y la máquina de estados deriva a un
estado Fail. Un fallo que nadie ve no es una compuerta, es un adorno.
"""
from __future__ import annotations

import json
import os
import time

import estado_s3

# `harvester` lee RAW_DIR en tiempo de import y en Lambda solo /tmp es
# escribible, así que esto va ANTES de importar el pipeline.
os.environ.setdefault("PRECIOVIVO_RAW", "/tmp/raw")
os.environ.setdefault("PRECIOVIVO_DB", estado_s3.RUTA_DB)

from preciovivo import harvester as H
from preciovivo.ingest import MIN_ROWS, _ingest_one
from preciovivo.store import Store

DIAS = int(os.environ.get("DIAS_A_COSECHAR", "5"))


def handler(event, _context):
    t0 = time.perf_counter()
    os.makedirs("/tmp/raw", exist_ok=True)
    dias = int((event or {}).get("dias", DIAS))

    etag = estado_s3.bajar_db()
    primera_vez = etag is None

    store = Store()
    store.init_schema()  # idempotente: CREATE TABLE IF NOT EXISTS

    objetivos = H.latest_dailies(dias)
    cargados, bloqueados, mensajes = 0, 0, []
    for d in objetivos:
        try:
            bien, msg = _ingest_one(store, d)
        except Exception as e:  # noqa: BLE001 - un día roto no cancela los demás
            bien, msg = False, f"ERROR {d.fecha}: {type(e).__name__}: {e}"
        mensajes.append(msg)
        cargados += bien
        bloqueados += not bien

    fechas = store.distinct_fechas()
    filas = store.count("precios_diarios")
    store.close()

    estado_s3.subir_db(etag)

    # LA CONDICIÓN DE FALLO, y por qué es ésta.
    #
    # "cero cargados" NO es un fallo por sí solo: un sábado, o un lunes antes de
    # que MIDAGRI publique, no hay reporte nuevo y no haber cargado nada es la
    # respuesta correcta. Marcarlo como error entrenaría a cualquiera a ignorar
    # las alarmas, que es peor que no tenerlas.
    #
    # El fallo de verdad es haber ENCONTRADO reportes y que TODOS quedaran
    # bloqueados: eso significa que los PDFs están ahí y ya no sabemos leerlos.
    # Es la firma de un cambio de layout, que es exactamente contra lo que existe
    # la compuerta MIN_ROWS.
    fallo_de_compuerta = bool(objetivos) and cargados == 0 and bloqueados > 0

    resultado = {
        "cargados": cargados,
        "bloqueados": bloqueados,
        "encontrados": len(objetivos),
        "fallo_de_compuerta": fallo_de_compuerta,
        "min_rows": MIN_ROWS,
        "primera_vez": primera_vez,
        "filas_precio": filas,
        "fechas": len(fechas),
        "ultima_fecha": fechas[-1] if fechas else None,
        "mensajes": mensajes,
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False, default=str))
    return resultado
