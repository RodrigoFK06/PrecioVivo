#!/usr/bin/env python
"""Entrada de la app CDK de Precio Vivo.

Antes de sintetizar, ensambla en `build/` un directorio por paquete de Lambda con
las fuentes que cada uno necesita. Las DEPENDENCIAS no se instalan aquí: las
instala CDK dentro de la imagen oficial del runtime (ver `_activo`).

POR QUÉ SE COPIA `preciovivo` Y NO SE IMPORTA DESDE pipeline/
--------------------------------------------------------------
Una Lambda se despliega como un zip autocontenido; no puede alcanzar un
directorio hermano del repositorio. La alternativa sería publicar `preciovivo`
como paquete e instalarlo, que a esta escala es ceremonia.

El riesgo de copiar es la deriva: dos copias del mismo archivo que se separan.
Se ataja de dos formas: la copia se REGENERA en cada síntesis (nunca se edita a
mano) y `build/` está en .gitignore, así que no existe una segunda versión que
alguien pueda tocar por error.

POR QUÉ LAS DEPENDENCIAS SE INSTALAN EN DOCKER Y NO AQUÍ
---------------------------------------------------------
Comprobado: `pip install --target` desde este Windows con Python 3.14 deja el
bytecode como `.cpython-314.pyc`. El runtime de Lambda es 3.13, así que esos
archivos son 33 MB de peso muerto Y no hay precompilación válida — cada arranque
en frío recompila. Instalando dentro de `public.ecr.aws/lambda/python:3.13` las
ruedas son las de Linux y el bytecode es el correcto.
"""
import pathlib
import shutil

import aws_cdk as cdk
from aws_cdk import BundlingOptions
from aws_cdk import aws_lambda as lambda_

from precio_vivo.fase1_stack import Fase1Stack
from precio_vivo.fase3_stack import Fase3Stack
from precio_vivo.sonda_waf_stack import SondaWafStack

RAIZ = pathlib.Path(__file__).resolve().parent.parent
AQUI = pathlib.Path(__file__).resolve().parent
BUILD = AQUI / "build"
PAQUETE = RAIZ / "pipeline" / "preciovivo"

# Solo estos tres para la Fase 1. `service.py` importa `corpus` para
# `cargar_snapshot`; el resto del paquete (retrieval, embeddings, vectorstore)
# arrastraría numpy y no hace falta para los endpoints de lectura. Comprobado:
# importar `service` y ejecutar los tres endpoints no carga NINGÚN paquete de
# terceros.
MODULOS_FASE1 = ["__init__.py", "service.py", "corpus.py"]

# Las dependencias de cada paquete, medidas ya podadas (límite de Lambda: 250 MB
# sin comprimir):
#   ingesta   requests + pdfplumber              54,4 MB
#   export    numpy + holidays                   54,9 MB
#   forecast  + scikit-learn (arrastra scipy)   173,6 MB
#
# El export NO lleva scikit-learn y eso no es un descuido: `forecast` lo importa
# dentro de `_nuevo_gbm`, no en el módulo. Con el caché ya escrito por el paso
# anterior ese camino nunca se recorre. Un import perezoso escrito por otra razón
# acaba decidiendo el empaquetado; por eso se mide en vez de suponer.
REQUISITOS = {
    "ingesta": ["requests==2.34.2", "pdfplumber==0.11.10"],
    "forecast": ["numpy==2.5.0", "scikit-learn==1.9.0", "holidays==0.99"],
    "export": ["numpy==2.5.0", "holidays==0.99"],
    "sonda": ["requests==2.34.2"],
}

# Se poda `tests/` (60 MB en el paquete del forecast) pero NO `__pycache__`:
# instalado en Docker, ese bytecode es el bueno y ahorra recompilar en cada
# arranque en frío — que con 20 contenedores del Map se paga 20 veces.
#
# Y NO se podan los `.dist-info`, aunque sea tentador. Se intentó y rompió la
# ejecución real con:
#     Unable to import module 'forecast_lambda':
#     No package metadata was found for holidays
# `holidays` lee su propia versión con `importlib.metadata`, que resuelve
# justamente contra ese directorio. Ahorraba 1 MB de 208 y costaba que el
# paquete no importara. La lección general: los `.dist-info` no son metadatos
# muertos, hay librerías que se introspeccionan en tiempo de import.
PODA = (
    " && find /asset-output -name tests -type d -prune -exec rm -rf {} +"
    " && find /asset-output -name '*.pyi' -delete"
)


def _limpiar_build() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)


def _ensamblar(nombre: str, fuentes: list[pathlib.Path],
               modulos: list[str] | None = None) -> str:
    """Copia fuentes + el paquete `preciovivo` a build/<nombre>. Devuelve la ruta.

    `modulos` limita qué archivos de `preciovivo` se copian; None copia el
    paquete entero (lo que necesitan ingesta, forecast y export, que importan
    harvester/parser/store/forecast/export entre ellos).
    """
    destino = BUILD / nombre
    destino.mkdir(parents=True)
    for f in fuentes:
        shutil.copy2(f, destino / f.name)
    if modulos is None:
        shutil.copytree(PAQUETE, destino / "preciovivo",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    elif modulos:
        (destino / "preciovivo").mkdir()
        for m in modulos:
            shutil.copy2(PAQUETE / m, destino / "preciovivo" / m)
    return str(destino)


def _activo(nombre: str) -> lambda_.Code:
    """El código de una Lambda, con sus dependencias instaladas en Docker.

    Sin `requirements.txt` en disco: las dependencias van en el propio comando,
    que las deja a la vista de quien lea este archivo en vez de en un archivo
    suelto que hay que ir a buscar.
    """
    reqs = " ".join(f"'{r}'" for r in REQUISITOS[nombre])
    return lambda_.Code.from_asset(
        str(BUILD / nombre),
        bundling=BundlingOptions(
            # `bundling_image` es la imagen de BUILD de SAM, no la del runtime.
            # La del runtime entra por su ENTRYPOINT al handler y es mínima: no
            # se puede dar por hecho que traiga pip ni find. Ésta está pensada
            # justo para esto y es la que documenta CDK.
            image=lambda_.Runtime.PYTHON_3_13.bundling_image,
            command=["bash", "-c",
                     f"pip install --quiet --target /asset-output {reqs}"
                     # `cp -r` y no `cp -au`: el ejemplo de la documentación de
                     # CDK usa -a, que intenta preservar dueño y timestamps. En
                     # un bind mount de Docker Desktop sobre Windows, corriendo
                     # como UID 1000, eso falla con "Operation not permitted" y
                     # tumba el bundling entero.
                     f" && cp -r . /asset-output" + PODA],
        ),
    )


_limpiar_build()

# Fase 1: solo el handler de consultas y tres módulos. Sin dependencias.
_ensamblar("lambda", [AQUI / "lambda" / "handler.py"], MODULOS_FASE1)

_COMUN = AQUI / "lambda_comun" / "estado_s3.py"
_ensamblar("ingesta", [AQUI / "lambda_ingesta" / "ingesta.py", _COMUN])
_ensamblar("forecast", [AQUI / "lambda_forecast" / "forecast_lambda.py", _COMUN])
_ensamblar("export", [AQUI / "lambda_export" / "exportar.py", _COMUN])
# La sonda del WAF lleva el harvester REAL, no una reimplementación: una sonda
# con su propia versión de la cadena de peticiones no responde la pregunta.
_ensamblar("sonda", [AQUI / "lambda_sonda" / "sonda.py", PAQUETE / "harvester.py"],
           modulos=[])

app = cdk.App()
entorno = cdk.Environment(region="us-east-1")

fase1 = Fase1Stack(
    app, "PrecioVivoFase1", env=entorno,
    description="Precio Vivo Fase 1: snapshot en S3 servido por Lambda con Function URL",
)
Fase3Stack(
    app, "PrecioVivoFase3", env=entorno,
    bucket=fase1.bucket,
    activos={n: _activo(n) for n in ("ingesta", "forecast", "export")},
    description="Precio Vivo Fase 3: ingesta diaria con EventBridge Scheduler y Step Functions",
)
SondaWafStack(
    app, "PrecioVivoSondaWaf", env=entorno,
    activo=_activo("sonda"),
    description="Sonda desechable: comprueba si el WAF de gob.pe bloquea a Lambda",
)
app.synth()
