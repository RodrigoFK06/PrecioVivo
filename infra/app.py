#!/usr/bin/env python
"""Entrada de la app CDK de Precio Vivo.

Antes de sintetizar, ensambla `build/lambda/` con el handler y los módulos de
`pipeline/preciovivo` que la función necesita.

POR QUÉ SE COPIA Y NO SE IMPORTA DESDE pipeline/
------------------------------------------------
Una Lambda se despliega como un zip autocontenido; no puede alcanzar un
directorio hermano del repositorio. La alternativa sería publicar `preciovivo`
como paquete e instalarlo, que a esta escala es ceremonia.

El riesgo de copiar es la deriva: dos copias del mismo archivo que se separan.
Se ataja de dos formas: la copia se REGENERA en cada síntesis (nunca se edita a
mano) y `build/` está en .gitignore, así que no existe una segunda versión que
alguien pueda tocar por error.
"""
import pathlib
import shutil

import aws_cdk as cdk

from precio_vivo.fase1_stack import Fase1Stack

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DESTINO = pathlib.Path(__file__).resolve().parent / "build" / "lambda"

# Solo estos tres. `service.py` importa `corpus` para `cargar_snapshot`; el
# resto del paquete (retrieval, embeddings, vectorstore) arrastraría numpy y no
# hace falta para los endpoints de lectura. Comprobado: importar `service` y
# ejecutar los tres endpoints no carga NINGÚN paquete de terceros.
MODULOS = ["__init__.py", "service.py", "corpus.py"]


def ensamblar() -> None:
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    (DESTINO / "preciovivo").mkdir(parents=True)
    shutil.copy2(pathlib.Path(__file__).parent / "lambda" / "handler.py", DESTINO)
    for m in MODULOS:
        shutil.copy2(RAIZ / "pipeline" / "preciovivo" / m, DESTINO / "preciovivo" / m)


ensamblar()

app = cdk.App()
Fase1Stack(
    app, "PrecioVivoFase1",
    env=cdk.Environment(region="us-east-1"),
    description="Precio Vivo Fase 1: snapshot en S3 servido por Lambda con Function URL",
)
app.synth()
