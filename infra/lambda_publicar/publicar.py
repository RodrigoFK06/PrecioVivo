"""Publica el snapshot y el índice reciente en el repositorio. Vercel hace el resto.

QUÉ SUSTITUYE
-------------
Las últimas cinco líneas de `scripts/actualizar-diario.ps1`:

    git add web/data/snapshot.json web/data/rag-reciente.*
    git commit -m "data: snapshot $fecha"
    git push

Eso es lo único que quedaba atando la publicación a una máquina encendida en
Lima. El sitio en Vercel se redespliega solo con el push, así que no hace falta
tocar nada del lado del sitio.

POR QUÉ COMMITEAR Y NO SERVIR DESDE S3
---------------------------------------
El sitio lee `web/data/snapshot.json` en tiempo de build y se despliega estático.
Cambiar eso por una descarga desde S3 significaría rehacer cómo se construye el
sitio, y a cambio de nada: el repositorio ya es la fuente que Vercel observa, y
un commit por día hábil es exactamente lo que lleva haciendo meses.

Lo que sí cambia es quién hace el commit: antes tu portátil, ahora una función
con un token de alcance mínimo.

UN COMMIT, NO TRES
------------------
Se usa la API de datos de Git —blobs, árbol, commit, ref— en vez de la API de
contenidos, que sube un archivo por llamada y produciría tres commits para un
solo cambio. Es más código y es el correcto: los tres archivos describen el mismo
día y deben entrar o quedarse fuera juntos.

SIN DEPENDENCIAS
----------------
Solo `urllib` de la biblioteca estándar. La API de GitHub es HTTP con JSON y no
necesita nada más; añadir `requests` serían 3 MB para no escribir seis líneas.
El zip de esta función son unos kilobytes.

QUÉ PASA SI NADIE CAMBIÓ NADA
------------------------------
Se compara el contenido con lo que ya está en el repositorio y, si coincide, NO
se commitea. Un sábado sin reporte nuevo no debe producir un commit vacío ni un
redespliegue de Vercel. El script de Windows hacía lo mismo con
`git diff --cached --quiet`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

import boto3

BUCKET = os.environ["BUCKET_SNAPSHOT"]
CLAVE_SNAPSHOT = os.environ.get("CLAVE_SNAPSHOT", "snapshot.json")
PREFIJO_RAG = os.environ.get("PREFIJO_RAG", "rag/")
PARAM_TOKEN = os.environ["PARAM_TOKEN_GITHUB"]
REPO = os.environ.get("REPO_GITHUB", "RodrigoFK06/PrecioVivo")
RAMA = os.environ.get("RAMA_GITHUB", "main")

# Qué objeto de S3 va a qué ruta del repositorio.
A_PUBLICAR = {
    CLAVE_SNAPSHOT: "web/data/snapshot.json",
    PREFIJO_RAG + "rag-reciente.bin": "web/data/rag-reciente.bin",
    PREFIJO_RAG + "rag-reciente.json.gz": "web/data/rag-reciente.json.gz",
}

API = "https://api.github.com"

_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")


def _leer_param(cliente, nombre: str, para_que: str, como: str) -> str:
    """Lee un SecureString de Parameter Store con un error que dice el arreglo.

    `ParameterNotFound` a secas obliga a ir a buscar qué parámetro falta y cómo
    se crea. El mensaje de abajo trae las dos cosas.
    """
    try:
        return cliente.get_parameter(Name=nombre, WithDecryption=True)["Parameter"]["Value"]
    except cliente.exceptions.ParameterNotFound:
        raise RuntimeError(
            f"falta el parámetro {nombre} ({para_que}). Se crea una vez con:\n"
            f"    {como}\n"
            f"No lo gestiona CDK a propósito: un secreto en la definición de la "
            f"infraestructura acaba en el repositorio y en los logs de despliegue."
        ) from None


def _token() -> str:
    return _leer_param(
        _ssm, PARAM_TOKEN, "token de GitHub para commitear el snapshot",
        f'aws ssm put-parameter --name "{PARAM_TOKEN}" --type SecureString '
        f'--value "<token>" --overwrite')


def _api(token: str, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "precio-vivo-publicador",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # El cuerpo del error de GitHub dice QUÉ permiso falta; sin él, un 403 es
        # indistinguible de un token caducado.
        detalle = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"GitHub {metodo} {ruta} -> {e.code}: {detalle}") from e


def _sha_git(datos: bytes) -> str:
    """El SHA-1 que Git le daría a este contenido como blob.

    Sirve para saber si el archivo cambió SIN subirlo: se compara contra el sha
    que el árbol del repositorio ya declara. Es lo que evita el commit vacío.
    """
    cabecera = f"blob {len(datos)}\0".encode()
    return hashlib.sha1(cabecera + datos).hexdigest()


def handler(_event, _context):
    t0 = time.perf_counter()
    token = _token()

    contenidos = {}
    for clave, ruta_repo in A_PUBLICAR.items():
        contenidos[ruta_repo] = _s3.get_object(Bucket=BUCKET, Key=clave)["Body"].read()

    ref = _api(token, "GET", f"/repos/{REPO}/git/ref/heads/{RAMA}")
    sha_commit = ref["object"]["sha"]
    commit = _api(token, "GET", f"/repos/{REPO}/git/commits/{sha_commit}")
    sha_arbol = commit["tree"]["sha"]

    # Qué hay hoy en el repositorio, para no commitear lo idéntico.
    arbol = _api(token, "GET", f"/repos/{REPO}/git/trees/{sha_arbol}?recursive=1")
    ya_esta = {e["path"]: e.get("sha") for e in arbol.get("tree", [])}

    cambios = {r: d for r, d in contenidos.items() if ya_esta.get(r) != _sha_git(d)}
    if not cambios:
        return {"commiteado": False,
                "motivo": "los tres archivos son idénticos a los del repositorio",
                "ms": round((time.perf_counter() - t0) * 1000)}

    entradas = []
    for ruta_repo, datos in cambios.items():
        blob = _api(token, "POST", f"/repos/{REPO}/git/blobs", {
            "content": base64.b64encode(datos).decode(),
            "encoding": "base64",
        })
        entradas.append({"path": ruta_repo, "mode": "100644", "type": "blob",
                         "sha": blob["sha"]})

    arbol_nuevo = _api(token, "POST", f"/repos/{REPO}/git/trees", {
        "base_tree": sha_arbol, "tree": entradas})

    fecha = time.strftime("%Y-%m-%d", time.gmtime())
    commit_nuevo = _api(token, "POST", f"/repos/{REPO}/git/commits", {
        "message": f"data: snapshot {fecha}",
        "tree": arbol_nuevo["sha"],
        "parents": [sha_commit],
    })

    # Sin `force`: si alguien empujó a main mientras trabajábamos, esto falla con
    # 422 en vez de borrar su commit. Es el mismo bloqueo optimista que el ETag
    # de S3, aplicado a Git.
    _api(token, "PATCH", f"/repos/{REPO}/git/refs/heads/{RAMA}",
         {"sha": commit_nuevo["sha"], "force": False})

    resultado = {
        "commiteado": True,
        "sha": commit_nuevo["sha"][:10],
        "mensaje": f"data: snapshot {fecha}",
        "archivos": {r: len(d) for r, d in cambios.items()},
        "sin_cambios": [r for r in contenidos if r not in cambios],
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    print(json.dumps(resultado, ensure_ascii=False))
    return resultado
