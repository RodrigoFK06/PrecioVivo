"""Lambda que sirve consultas de Precio Vivo sobre el snapshot.

LA DECISION CENTRAL DE ESTA FUNCION
-----------------------------------
El snapshot (~3,4 MB de JSON) se descarga y se parsea EN EL AMBITO DEL MODULO,
no dentro del handler. Lambda ejecuta el modulo una vez por contenedor y luego
CONGELA el proceso entre invocaciones, asi que ese trabajo se paga una sola vez
y las peticiones siguientes responden desde memoria.

Medido en CloudWatch sobre el despliegue real:
    Init Duration (frio) : 707,5 ms
    Duration p50         :   1,78 ms
    ratio                :    397x

CONSECUENCIA DE COSTO, que es el argumento de verdad: multiplicar el trafico por
100 NO multiplica por 100 las lecturas a S3, porque se lee una vez por
contenedor y no una vez por peticion.

Lo que NO garantiza: "caliente" no es un estado permanente. AWS recicla
contenedores cuando quiere, asi que la proporcion de arranques en frio es una
propiedad estadistica del trafico. De ahi la telemetria de abajo.

EL LIMITE DE ESTE DISENO, medido:
    72 productos   3,5 MB    150 ms de parseo
    1.008          48,5 MB   2,4 s     <- aqui ya no es aceptable
Por encima de ~15 MB de snapshot hay que migrar a acceso por clave.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boto3  # noqa: E402 - viene en el runtime de Lambda, no se empaqueta

from preciovivo import service  # noqa: E402
from preciovivo.service import ErrorDeConsulta  # noqa: E402

BUCKET = os.environ["SNAPSHOT_BUCKET"]
CLAVE = os.environ.get("SNAPSHOT_KEY", "snapshot.json")
TABLA = os.environ.get("TABLA_TELEMETRIA", "")
# Interruptor. Existe para poder MEDIR cuanto cuesta la propia telemetria: se
# compara la p50 con y sin escritura. Una telemetria cuyo coste no conoces es
# una que no puedes defender.
TELEMETRIA = os.environ.get("TELEMETRIA", "1") == "1"
DIAS_TTL = int(os.environ.get("DIAS_TTL", "30"))

# --- Carga en frio: UNA vez por contenedor ---------------------------------
_t0 = time.perf_counter()
_s3 = boto3.client("s3")
_cuerpo = _s3.get_object(Bucket=BUCKET, Key=CLAVE)["Body"].read()
SNAPSHOT = json.loads(_cuerpo)
MS_ARRANQUE = (time.perf_counter() - _t0) * 1000
BYTES_SNAPSHOT = len(_cuerpo)
del _cuerpo

# El cliente de DynamoDB tambien fuera del handler: construirlo carga el modelo
# del servicio (~50 ms) y dentro del handler se pagaria en CADA invocacion.
_ddb = boto3.client("dynamodb") if (TELEMETRIA and TABLA) else None

_PRIMERA = True


def _emf(plantilla, producto, ms, frio, estado, ms_escritura=0.0):
    """Metrica por Embedded Metric Format: CloudWatch la indexa desde el log.

    No hay llamada a PutMetricData, asi que no anade latencia ni permisos de
    escritura al rol.

    OJO CON LA DIMENSION. Aqui va la PLANTILLA de ruta ("/productos/{slug}"), no
    la ruta concreta ("/productos/papa-blanca"). CloudWatch cobra por serie
    temporal unica: con el slug dentro, 72 productos son 148 series (~44 USD/mes)
    y 1.008 productos son 2.020 (~606 USD/mes). El producto concreto viaja como
    campo suelto del log: se puede leer y filtrar, pero no genera serie.

    Esa es la frontera: baja cardinalidad a dimension, alta cardinalidad a campo.
    Este bug estaba en la Fase 1 y se corrigio aqui.
    """
    registro = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "PrecioVivo",
                "Dimensions": [["Endpoint"]],
                "Metrics": [
                    {"Name": "LatenciaMs", "Unit": "Milliseconds"},
                    {"Name": "ColdStart", "Unit": "Count"},
                ],
            }],
        },
        "Endpoint": plantilla,
        "LatenciaMs": round(ms, 3),
        "ColdStart": 1 if frio else 0,
        "estado": estado,
        "msArranque": round(MS_ARRANQUE, 1),
    }
    if producto:
        registro["producto"] = producto  # campo, NO dimension
    if ms_escritura:
        # Campo, no metrica: interesa para el analisis puntual del coste de la
        # telemetria, no como serie temporal permanente.
        registro["msEscrituraTelemetria"] = round(ms_escritura, 3)
    print(json.dumps(registro))


def _telemetria(plantilla, producto, ms, frio, estado):
    """Registro crudo en DynamoDB.

    POR QUE UNA TABLA SI CLOUDWATCH YA DA p95 Y PORCENTAJE DE FRIOS
    ---------------------------------------------------------------
    Por la cardinalidad. CloudWatch cobra por serie temporal, asi que no puedes
    poner "producto" como dimension sin que el coste explote. DynamoDB guarda el
    registro crudo por centimos y permite re-agregar despues por cualquier campo,
    incluso por preguntas que hoy no existen. CloudWatch para agregados en
    caliente de baja cardinalidad; DynamoDB para el detalle.

    LAS CLAVES
    ----------
        PK = endpoint#fecha     "/comparar#2026-08-18"
        SK = timestamp#id       "2026-08-18T04:12:07.412Z#a3f9"

    La PK de DynamoDB solo admite IGUALDAD: no hay rangos sobre ella. Eso
    descarta "PK = timestamp", que dejaria "las ultimas 24 h" sin mas salida que
    un Scan de la tabla entera.

    La alternativa razonable era "PK = fecha", "SK = endpoint#timestamp#id": con
    ella "todo el dia D" es UNA Query y "el endpoint X del dia D" tambien, usando
    begins_with. Responde mas preguntas con menos consultas. Se descarto porque
    concentra todas las escrituras del dia en una particion, y el tope es de
    1.000 escrituras por segundo.

    Honestidad sobre esa decision: a 10.000 peticiones/mes se escriben 0,004 por
    segundo. Estamos a 250.000x del limite. La eleccion es correcta por una razon
    que hoy NO aplica; lo que la justifica es que sigue siendo correcta cuando el
    volumen suba, sin migrar nada.

    COSTE DE LA ELECCION: el p95 global necesita una Query por endpoint. Con 3
    endpoints son 3; con 30 serian 30. Si el numero de endpoints creciera mucho,
    la alternativa descartada se volveria la buena.

    El "#id" del SK no es adorno: dos llamadas en el mismo milisegundo tendrian
    la misma clave y una sobrescribiria a la otra.
    """
    if _ddb is None:
        return 0.0
    ahora = time.time()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ahora)) + f".{int(ahora % 1 * 1000):03d}Z"
    fecha = ts[:10]
    item = {
        "pk": {"S": f"{plantilla}#{fecha}"},
        "sk": {"S": f"{ts}#{uuid.uuid4().hex[:8]}"},
        "endpoint": {"S": plantilla},
        "fecha": {"S": fecha},
        "latencia_ms": {"N": f"{ms:.3f}"},
        # 0/1 en vez de booleano: asi el PROMEDIO de esta columna es directamente
        # la proporcion de arranques en frio, sin transformar nada al leer.
        "frio": {"N": "1" if frio else "0"},
        "estado": {"N": str(estado)},
        # DynamoDB borra por TTL sin cobrar. La telemetria no necesita ser eterna.
        "ttl": {"N": str(int(ahora) + DIAS_TTL * 86400)},
    }
    if producto:
        item["producto"] = {"S": producto}
    t = time.perf_counter()
    try:
        _ddb.put_item(TableName=TABLA, Item=item)
    except Exception as e:  # noqa: BLE001
        # La telemetria NUNCA puede tumbar la respuesta. Si DynamoDB falla, se
        # deja rastro en el log y la peticion sigue su curso.
        print(f"TELEMETRIA fallo: {type(e).__name__}: {e}")
    # Devuelve lo que costo ESCRIBIR. Es la respuesta a "cuanto me cuesta mi
    # propia telemetria", y sin medirla desde dentro no se puede separar del
    # ruido del runtime.
    return (time.perf_counter() - t) * 1000


def _json(estado, cuerpo, frio):
    return {
        "statusCode": estado,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "x-cold-start": "1" if frio else "0",
            "x-snapshot-bytes": str(BYTES_SNAPSHOT),
        },
        "body": json.dumps(cuerpo, ensure_ascii=False, default=str),
    }


def _rutear(metodo, ruta, q):
    """Enruta a service.py. Ninguna logica de negocio vive aqui.

    Devuelve (cuerpo, estado, plantilla, producto). La PLANTILLA es la ruta con
    el parametro sin sustituir; es lo que se usa como dimension de metrica y como
    clave de particion, para no multiplicar series ni particiones por producto.
    """
    if metodo != "GET":
        return {"error": f"metodo {metodo} no soportado"}, 405, "desconocida", None

    partes = [p for p in ruta.split("/") if p]

    if partes == ["health"]:
        return {"estado": "ok", "dataset": service.meta(SNAPSHOT)}, 200, "/health", None

    # Puntual por clave.
    if len(partes) == 2 and partes[0] == "productos":
        return (service.precio_actual(partes[1], SNAPSHOT), 200,
                "/productos/{slug}", partes[1])

    # Rango sobre clave de ordenamiento.
    if len(partes) == 3 and partes[0] == "productos" and partes[2] == "serie":
        return (service.serie_historica(partes[1], q.get("desde"), q.get("hasta"), SNAPSHOT),
                200, "/productos/{slug}/serie", partes[1])

    # Dispersion y recoleccion.
    if partes == ["comparar"]:
        productos = [p for p in q.get("productos", "").split(",") if p.strip()]
        dias = int(q.get("dias", 30))
        return (service.comparar(productos, dias, SNAPSHOT), 200,
                "/comparar", ",".join(productos)[:120] or None)

    return ({"error": f"ruta desconocida: {ruta}",
             "rutas": ["/health", "/productos/{slug}",
                       "/productos/{slug}/serie", "/comparar?productos=a,b"]},
            404, "desconocida", None)


def handler(event, _context):
    global _PRIMERA
    frio, _PRIMERA = _PRIMERA, False

    http = (event.get("requestContext") or {}).get("http") or {}
    metodo = http.get("method", "GET")
    ruta = event.get("rawPath", "/")
    q = event.get("queryStringParameters") or {}

    plantilla, producto = "desconocida", None
    t = time.perf_counter()
    try:
        cuerpo, estado, plantilla, producto = _rutear(metodo, ruta, q)
    except ErrorDeConsulta as e:
        # Error de dominio: culpa del cliente, y el mensaje trae sugerencias del
        # tipo "coincide con 10 productos: papa-blanca, papa-amarilla...".
        cuerpo, estado = {"error": str(e)}, 404
        trozos = [p for p in ruta.split("/") if p]
        plantilla = "/productos/{slug}"
        producto = trozos[1] if len(trozos) > 1 else None
    except Exception as e:  # noqa: BLE001 - nada debe devolver un 502 opaco
        cuerpo, estado = {"error": f"fallo interno: {type(e).__name__}"}, 500
        print(f"ERROR no controlado en {ruta}: {type(e).__name__}: {e}")

    # Se mide ANTES de la telemetria: latencia_ms es lo que tarda la consulta, no
    # lo que tarda la consulta mas registrarla. Mezclarlas haria que la metrica
    # midiera su propio coste.
    ms = (time.perf_counter() - t) * 1000
    ms_escritura = _telemetria(plantilla, producto, ms, frio, estado)
    # El EMF va DESPUES para poder incluir lo que costo la escritura.
    _emf(plantilla, producto, ms, frio, estado, ms_escritura)
    return _json(estado, cuerpo, frio)
