"""El forecast repartido: un producto por invocación, más un paso de reducción.

POR QUÉ ESTÁ PARTIDO
--------------------
Medido sobre la BD real (73 productos):

    forecast_producto (h=1)   619,8 s
    _comparacion_modelos      629,1 s
    _forecast_7d              ~mismo orden que h=1
    _kill_gate                  0,5 s
    --------------------------------
    total                     ~31 min

El límite duro de una Lambda son 15 minutos y no se puede subir. O sea que esto
no cabe por el doble, no por poco. (De paso: el comentario de
`actualizar-diario.ps1` dice "~14 min", y está desactualizado — el walk-forward
crece con la historia.)

Lo que sí cabe: el trabajo de CADA producto son ~25 s sumando los cuatro
cálculos. Se reparte con un `Map` de Step Functions y el reloj baja a ~1 minuto.
Y lo que importa más que el minuto: el trabajo por unidad se queda pequeño
aunque la historia crezca 10x, que es justo el plan.

LO QUE NO SE TOCÓ
-----------------
El anti-leakage. `_comparacion_producto` es el cuerpo del bucle de
`_comparacion_modelos` extraído a una función: mismas llamadas, mismos
argumentos, mismo orden. El GBM se sigue re-ajustando por origen y `_impute_vol`
sigue haciendo forward-fill causal. Verificado diffeando la salida completa
contra la de antes del refactor.

LA CACHÉ DE LA BD, que es el argumento de la Fase 1 otra vez
------------------------------------------------------------
Los ~73 trabajadores corren en unos 20 contenedores. Descargar la SQLite en cada
invocación serían 73 descargas; cacheándola en el ámbito del módulo son ~20.
Pero una caché sin invalidar sirve datos viejos cuando el contenedor sobrevive al
día, así que se compara el ETag con un HeadObject — una llamada barata que no
descarga el objeto. Es exactamente la respuesta que da la Fase 1 a "¿y si el
snapshot cambia mientras el contenedor sigue vivo?", aplicada.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date

import boto3

import estado_s3

os.environ.setdefault("PRECIOVIVO_DB", estado_s3.RUTA_DB)

from preciovivo import forecast as F  # noqa: E402
from preciovivo.store import Store  # noqa: E402

_s3 = boto3.client("s3")

_etag_en_disco: str | None = None
_series: dict | None = None


def _cargar_series() -> tuple[dict, str]:
    """Devuelve ({nombre: series}, etag), reusando la descarga entre invocaciones.

    El HeadObject cuesta una fracción de un GET y evita bajar 7,9 MB cuando el
    contenedor ya tiene la versión buena. Si el ETag cambió, se descarga otra vez.
    """
    global _etag_en_disco, _series
    etag = _s3.head_object(Bucket=estado_s3.BUCKET, Key=estado_s3.CLAVE_DB)["ETag"]
    if _series is not None and etag == _etag_en_disco:
        return _series, etag
    estado_s3.bajar_db()
    _etag_en_disco = etag
    _series = F._cargar_series(estado_s3.RUTA_DB)
    return _series, etag


def listar(_event, _context):
    """Primer paso: qué productos hay. Es lo que itera el Map."""
    series, _ = _cargar_series()
    nombres = list(series.keys())
    print(json.dumps({"productos": len(nombres)}))
    return {"nombres": nombres, "n": len(nombres)}


def producto(event, _context):
    """Un producto: pronóstico a 1 y 7 días, más su parte de la comparación.

    Los cuatro cálculos van juntos en la misma invocación a propósito: los cuatro
    parten de la MISMA serie ya cargada, y separarlos multiplicaría por cuatro las
    descargas de la BD para ahorrar unos segundos de un reloj que ya es de un
    minuto.
    """
    t0 = time.perf_counter()
    nombre = event["nombre"]
    series, _ = _cargar_series()
    s = series.get(nombre)
    if s is None:
        # No se inventa un resultado: si el producto desapareció entre el listado
        # y este paso, se dice y el agregado lo cuenta como no evaluable.
        return {"nombre": nombre, "ausente": True}

    salida = {
        "nombre": nombre,
        "f1": F.forecast_producto(s, F.HORIZONTE_DIAS),
        "f7": F.forecast_producto(s, 7),
        "comp": {str(h): F._comparacion_producto(s, h) for h in F.HORIZONTES},
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps({"nombre": nombre, "ms": salida["ms"],
                      "metodo": salida["f1"].get("metodo")}, ensure_ascii=False))
    return salida


def reducir(event, _context):
    """Agrega los resultados del Map y publica el caché que lee `export`.

    `_kill_gate` se calcula aquí y no en el Map porque son 0,5 s: repartirlo
    costaría más en orquestación que lo que tarda.
    """
    t0 = time.perf_counter()
    partes = [p for p in event.get("partes", []) if p and not p.get("ausente")]
    series, etag = _cargar_series()

    por_slug = {p["nombre"]: p["f1"] for p in partes}
    forecast_7d = {p["nombre"]: p["f7"] for p in partes}
    por_producto = {p["nombre"]: p["comp"] for p in partes}

    kill_gate = F._kill_gate(series)
    # El segundo argumento es lo que ya calculó el Map: aquí solo se agrega.
    kill_gate["comparacion_modelos"] = F._comparacion_modelos(series, por_producto)
    kill_gate["forecast_7d"] = forecast_7d

    resultado = {"por_slug": por_slug, "kill_gate": kill_gate}
    cuerpo = json.dumps(resultado, ensure_ascii=False).encode("utf-8")
    estado_s3.subir_cache_forecast(cuerpo)

    # Historial de pronósticos, igual que hace `ingest --forecast` en local.
    # Hoy no lo lee nadie: es el registro con el que MAÑANA se podrá medir el
    # acierto real contra lo que acabó pasando. Se replica porque una versión en
    # AWS que produzca menos que la local, sin decirlo, es justo lo que no puede
    # pasar. La escritura vuelve a ser condicional por el mismo motivo de siempre.
    store = Store()
    n_pron = store.upsert_pronosticos(date.today(), list(por_slug.items()))
    store.close()
    estado_s3.subir_db(etag)

    resumen = {
        "productos": len(por_slug),
        "ausentes": len(event.get("partes", [])) - len(partes),
        "pronosticos_guardados": n_pron,
        "bytes_cache": len(cuerpo),
        "ganador_1d": (kill_gate["comparacion_modelos"]
                       .get("por_horizonte", {}).get("1", {}).get("ganador")),
        "volume_helps": kill_gate.get("volume_helps"),
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resumen, ensure_ascii=False))
    return resumen
