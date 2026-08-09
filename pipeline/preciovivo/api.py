"""API REST de Precio Vivo (FastAPI). Transporte delgado sobre `service.py`.

QUÉ HACE Y QUÉ NO
-----------------
Este módulo hace tres cosas: traducir HTTP a llamadas de `service`, traducir
errores de dominio a códigos de estado, y describir el contrato en OpenAPI. La
lógica de negocio NO vive aquí — vive en `service.py`, que también usan el
servidor MCP y el agente. Si un endpoint tiene reglas propias, está mal ubicado.

AUTENTICACIÓN: FALLA CERRADA
----------------------------
Sin `PRECIOVIVO_API_KEYS` configurada, los endpoints de datos devuelven 503 en
vez de quedar abiertos. Un default abierto es la clase de decisión que nadie
revisa hasta que la API lleva meses pública: preferimos que rompa en el primer
request de desarrollo, donde se ve, y no en producción, donde no.

Para correr sin claves a propósito (dev, demos) está `PRECIOVIVO_API_ABIERTA=1`,
que es explícito y auditable. `/health` y la documentación siempre responden: si
la puerta está cerrada hay que poder preguntarle por qué.
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from . import service
from .service import ErrorDeConsulta

CABECERA_API_KEY = "X-API-Key"
_api_key_header = APIKeyHeader(name=CABECERA_API_KEY, auto_error=False)

ATRIBUCION = ("Fuente: MIDAGRI – GMML, procesado por Precio Vivo · "
              "cifras referenciales, no oficiales.")

DESCRIPCION = f"""
Inteligencia de precios mayoristas del **Gran Mercado Mayorista de Lima (GMML)**.

Serie diaria de ~73 productos desde julio de 2024: precios normalizados a S//kg,
volumen de ingreso en toneladas, tendencia publicada por la fuente, pronósticos
beta y detección estadística de anomalías.

### Honestidad de los datos
* Los **precios y volúmenes** son hechos derivados del reporte oficial.
* Los **pronósticos** son estimaciones de un modelo, marcadas como tales en cada
  respuesta. No son cifras de la fuente.
* Las **anomalías** son señales estadísticas de desvío, no explicaciones causales.
  El dato dice *qué* pasó, no *por qué*.

### Autenticación
Cabecera `{CABECERA_API_KEY}`. Sin claves configuradas, los endpoints de datos
responden 503 (falla cerrada, no abierta).

{ATRIBUCION}
"""


def _claves() -> set[str]:
    crudo = os.environ.get("PRECIOVIVO_API_KEYS", "")
    return {k.strip() for k in crudo.split(",") if k.strip()}


def _abierta() -> bool:
    return os.environ.get("PRECIOVIVO_API_ABIERTA", "").lower() in ("1", "true", "si")


async def exigir_clave(clave: Annotated[str | None, Depends(_api_key_header)]) -> str:
    """Dependencia de autenticación. Ver la nota de FALLA CERRADA del módulo."""
    claves = _claves()
    if not claves:
        if _abierta():
            return "abierta"
        raise HTTPException(
            status_code=503,
            detail=("La API no tiene claves configuradas y por eso no sirve datos. "
                    "Define PRECIOVIVO_API_KEYS=clave1,clave2 — o, si de verdad la "
                    "quieres pública, PRECIOVIVO_API_ABIERTA=1 de forma explícita."))
    if not clave:
        raise HTTPException(status_code=401,
                            detail=f"Falta la cabecera {CABECERA_API_KEY}.")
    if clave not in claves:
        raise HTTPException(status_code=403, detail="Clave de API inválida.")
    return clave


Auth = Annotated[str, Depends(exigir_clave)]

app = FastAPI(
    title="Precio Vivo API",
    description=DESCRIPCION,
    version="1.0.0",
    license_info={"name": "MIT"},
    contact={"name": "Árkos", "url": "https://www.xn--rkos-4na.com/"},
    openapi_tags=[
        {"name": "datos", "description": "Hechos numéricos derivados del reporte."},
        {"name": "modelo", "description": "Salidas de modelos: pronósticos y anomalías. Estimaciones, no datos de la fuente."},
        {"name": "ia", "description": "Consulta en lenguaje natural sobre el dataset (RAG)."},
        {"name": "meta", "description": "Estado del servicio y del dataset."},
    ],
)


@app.exception_handler(ErrorDeConsulta)
async def _error_de_consulta(request: Request, exc: ErrorDeConsulta) -> JSONResponse:
    """Los errores de dominio son culpa del cliente (404/400), no fallos internos.

    Se distinguen a propósito: un 500 manda a revisar nuestros logs; un 404 con
    sugerencias manda a corregir el slug, que es lo que hace falta.
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"], summary="Estado del servicio")
async def health() -> dict:
    """Sin autenticación a propósito: es lo que consulta un balanceador.

    Reporta el estado REAL del dataset, no un "ok" fijo: un servicio que responde
    200 mientras sirve datos de hace dos semanas es peor que uno caído, porque
    nadie se entera.
    """
    try:
        m = service.meta()
        estado = "ok"
        detalle = None
    except ErrorDeConsulta as e:
        m, estado, detalle = {}, "sin_datos", str(e)
    return {
        "estado": estado, "detalle": detalle,
        "dataset": m,
        "autenticacion": ("abierta" if (_abierta() and not _claves())
                          else "api-key" if _claves() else "sin-configurar"),
    }


@app.get("/meta", tags=["meta"], summary="Cobertura del dataset")
async def get_meta(_: Auth) -> dict:
    return service.meta()


@app.get("/mercados", tags=["meta"], summary="Mercados cubiertos")
async def get_mercados(_: Auth) -> dict:
    return service.mercados()


@app.get("/verificacion", tags=["meta"],
         summary="Cross-check contra SISAP-MIDAGRI")
async def get_verificacion(_: Auth) -> dict:
    """Segunda opinión sobre nuestra propia ingesta, contra una publicación
    independiente del mismo ministerio."""
    return service.verificacion()


# --------------------------------------------------------------------------- #
# datos
# --------------------------------------------------------------------------- #
@app.get("/productos", tags=["datos"], summary="Catálogo con el precio vigente")
async def get_productos(
    _: Auth,
    buscar: Annotated[str | None, Query(description="Filtra por nombre.")] = None,
) -> dict:
    items = service.listar_productos(buscar)
    return {"n": len(items), "productos": items, "atribucion": ATRIBUCION}


@app.get("/productos/{producto}", tags=["datos"], summary="Precio vigente")
async def get_producto(producto: str, _: Auth) -> dict:
    """`producto` acepta slug o nombre, con o sin acentos."""
    return service.precio_actual(producto)


@app.get("/productos/{producto}/serie", tags=["datos"], summary="Serie histórica")
async def get_serie(
    producto: str,
    _: Auth,
    desde: Annotated[str | None, Query(description="ISO YYYY-MM-DD.")] = None,
    hasta: Annotated[str | None, Query(description="ISO YYYY-MM-DD.")] = None,
) -> dict:
    return service.serie_historica(producto, desde, hasta)


@app.get("/comparar", tags=["datos"], summary="Comparar varios productos")
async def get_comparar(
    _: Auth,
    productos: Annotated[list[str], Query(description="Repetir el parámetro. Máx. 10.")],
    dias: Annotated[int, Query(ge=1, le=730, description="Ventana en días.")] = 30,
) -> dict:
    return service.comparar(productos, dias)


@app.get("/resumen", tags=["datos"], summary="Resumen del día y alertas")
async def get_resumen(_: Auth) -> dict:
    return service.resumen_dia()


# --------------------------------------------------------------------------- #
# modelo
# --------------------------------------------------------------------------- #
@app.get("/productos/{producto}/pronostico", tags=["modelo"],
         summary="Pronóstico (estimación beta)")
async def get_pronostico(producto: str, _: Auth) -> dict:
    """Cada respuesta incluye `advertencia` a propósito: quien consume por API no
    ve el disclaimer del dashboard."""
    return service.pronostico(producto)


@app.get("/anomalias", tags=["modelo"], summary="Anomalías estadísticas del día")
async def get_anomalias(
    _: Auth,
    producto: Annotated[str | None, Query(description="Filtra por producto.")] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    return service.anomalias(producto, limite)


# --------------------------------------------------------------------------- #
# ia
# --------------------------------------------------------------------------- #
@app.post("/consulta", tags=["ia"], summary="Pregunta en lenguaje natural")
async def post_consulta(
    _: Auth,
    pregunta: Annotated[str, Query(description="Pregunta en español.")],
    k: Annotated[int, Query(ge=1, le=30, description="Chunks a recuperar.")] = 8,
) -> dict:
    try:
        return service.consultar(pregunta, k)
    except RuntimeError as e:
        # Índice RAG ausente o incompatible: es un problema de despliegue, no del
        # cliente, y el mensaje ya dice cómo arreglarlo.
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/recuperar", tags=["ia"],
          summary="Contexto recuperado, sin llamar al modelo")
async def post_recuperar(
    _: Auth,
    pregunta: Annotated[str, Query(description="Pregunta en español.")],
    k: Annotated[int, Query(ge=1, le=30)] = 8,
) -> dict:
    """Devuelve la evidencia que el RAG seleccionó, sin generar texto.

    Existe por dos razones: hace auditable la respuesta del modelo (se ve
    exactamente qué leyó), y le permite al sitio usar ESTA implementación en vez
    de la copia en TypeScript de `web/lib/rag.ts`.
    """
    try:
        return service.recuperar(pregunta, k)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.environ.get("PRECIOVIVO_API_HOST", "127.0.0.1"),
                port=int(os.environ.get("PRECIOVIVO_API_PORT", "8000")))


if __name__ == "__main__":
    main()
