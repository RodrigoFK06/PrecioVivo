"""El contraste con SISAP: la segunda opinión sobre los precios del GMML.

QUÉ APORTA, QUE NO ES "MÁS DATOS"
----------------------------------
SISAP publica los mismos precios del GMML que el reporte 335, por otra vía y con
otro formato. Compararlos es lo único que convierte "estos son los precios" en
"estos son los precios y hay una segunda fuente que dice lo mismo". El bloque
`verificacion` del snapshot es esa diferencia.

Además ingesta AVES VIVAS (pollo vivo), que solo está en SISAP.

Lo que NO hace: upsertar los precios GMML de SISAP. Ya vienen del reporte 335, y
mezclarlos anularía justo la redundancia que da valor al contraste. Los de MMF2
tampoco: llegan por el boletín con otra semántica.

POR QUÉ ES UN PASO APARTE Y NO PARTE DE LA COSECHA
---------------------------------------------------
Porque falla distinto. SISAP publica a otra hora (~06:30) y también los sábados,
mientras que el GMML no publica fines de semana. Un fallo aquí NO puede tumbar la
publicación del snapshot: los precios del 335 ya están cargados y el sitio tiene
que salir igual, solo que sin el bloque de verificación.

En la máquina de estados eso es un `Catch` que sigue adelante. En el script de
Windows era un try/except con un Write-Warning; la diferencia es que aquí queda
en el historial de la ejecución en vez de en una consola que nadie mira.

EL DESFASE DE FECHAS, que es la parte sutil
--------------------------------------------
La fecha de SISAP va SIEMPRE por delante de nuestro último día del 335, porque
SISAP publica sábados y el GMML no. Ejecutar este paso después de la cosecha no
arregla eso. Lo resuelve `sisap._resolver_objetivo`, usando la columna "Precio
Ayer" de SISAP contra nuestro último día GMML: ambas describen la misma jornada.
Ese razonamiento vive en el pipeline y aquí no se toca.

Consecuencia práctica: `gmml_disponible = False` NO es un fallo. Significa que el
335 del día todavía no está en la BD y el contraste es prematuro, no divergente.

NOTA DE RED: SISAP se sirve por HTTP, no HTTPS
-----------------------------------------------
    http://sistemas.midagri.gob.pe/sisap/...
No es una elección nuestra y no existe versión con TLS. Se documenta en vez de
esconderse: es un PDF público, la petición no lleva credenciales, y el riesgo se
acota a que alguien en la ruta sirva un PDF falso — contra lo cual el parser
tiene sus propias comprobaciones estructurales.
"""
from __future__ import annotations

import json
import os
import time

import estado_s3

os.environ.setdefault("PRECIOVIVO_RAW", "/tmp/raw")
os.environ.setdefault("PRECIOVIVO_DB", estado_s3.RUTA_DB)

from preciovivo import sisap as S
from preciovivo.ingest import _sisap_aves_rows
from preciovivo.store import Store


def handler(_event, _context):
    t0 = time.perf_counter()
    os.makedirs("/tmp/raw", exist_ok=True)

    etag = estado_s3.bajar_db()
    if etag is None:
        raise RuntimeError(
            f"no hay BD en s3://{estado_s3.BUCKET}/{estado_s3.CLAVE_DB}: "
            "el contraste necesita los precios del 335 ya cargados")

    ruta, sha, bytes_pdf = S.fetch_sisap()
    filas = S.parse_sisap(ruta)
    fecha = filas[0]["fecha"] if filas else None
    if not filas or fecha is None:
        # Se devuelve en vez de lanzar: no hay nada roto, no hay nada que contrastar.
        return {"omitido": "sin filas o sin fecha en el PDF de SISAP",
                "bytes_pdf": bytes_pdf,
                "ms": round((time.perf_counter() - t0) * 1000)}

    store = Store()
    store.record_raw(S.FUENTE, fecha, S.SISAP_URL, sha, bytes_pdf)

    # 1) AVES VIVAS -> serie propia. Mismo helper que usa la CLI: si la lógica de
    #    qué filas son AVES cambia, cambia en un solo sitio.
    fecha_aves, prs = _sisap_aves_rows(filas)
    n_aves = 0
    if prs:
        mid = store.mercado_para_codigo("AVES")
        n_aves = store.upsert_precios(fecha_aves, prs, S.FUENTE, mercado_id=mid)
    store.close()

    # 2) Contraste, persistido para que `export` lo adjunte sin red ni re-parseo.
    cc = S.cross_check(filas, estado_s3.RUTA_DB)
    check = S.save_check(cc, estado_s3.RUTA_DB)
    estado_s3.subir_sisap()
    estado_s3.subir_db(etag)

    resultado = {
        "bytes_pdf": bytes_pdf,
        "filas_sisap": len(filas),
        "fecha_sisap": str(fecha),
        "aves_upserted": n_aves,
        "gmml_disponible": check.get("gmml_disponible"),
        "fecha_contraste": check.get("fecha"),
        "contrastados": check.get("contrastados"),
        "coinciden": check.get("coinciden"),
        "umbral_pct": check.get("umbral_pct"),
        "discrepancias": [
            {"producto": r["producto"], "sisap": r["sisap_kg"], "gmml": r["gmml_kg"]}
            for r in check.get("resultados", []) if r.get("flag")
        ],
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False, default=str))
    return resultado
