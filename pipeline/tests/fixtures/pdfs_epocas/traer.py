"""Baja las cuatro ediciones que fijan la compuerta de geometria.

Los PDF no se versionan (.gitignore:28, «LEGAL 0.5: never re-host source PDFs»).
Este script los trae para que `test_parser_geometria.py` tenga contra que fallar;
sin ellos las pruebas hacen skip, que es el patron ya usado en el repositorio.

    python pipeline/tests/fixtures/pdfs_epocas/traer.py

Cada URL se verifico por sha256 contra el archivo que se midio el 2026-08-25: no
se dedujo del nombre. La primera version de este script SI dedujo dos IDs del CDN
y dio 403 en ambos -- de ahi que aqui se comprueba la huella y no solo el 200.
"""
import hashlib
import os
import sys

import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (compatible; PrecioVivo/1.0)"}

# (nombre local, url verificada, sha256, ancho de pagina en puntos)
EDICIONES = [
    ("2019-11-04_612pt.pdf",
     "https://cdn.www.gob.pe/uploads/document/file/426813/"
     "sisap-ingreso-gmml-04nov19.pdf?v=1574776765",
     "0f59c2d080d1bf126ee2232b34bc1adcc93709f723a425c103460a5e14e3b8a3", 612.0),
    ("2021-01-04_612pt.pdf",
     "https://cdn.www.gob.pe/uploads/document/file/1537703/"
     "Reporte%20de%20Ingreso%20y%20Precios%20en%20el%20GRAN%20MERCADO%20"
     "MAYORISTA%20DE%20LIMA%20-%2004/01/21.pdf?v=1610158933",
     "93b85a5c19468e05809892cd9f7dddaa6918b79b55fcb73984350bb028e8be49", 612.0),
    ("2023-01-03_661pt.pdf",
     "https://cdn.www.gob.pe/uploads/document/file/4017083/"
     "Reporte%20de%20Ingreso%20y%20Precios%20en%20el%20GRAN%20MERCADO%20"
     "MAYORISTA%20DE%20LIMA%20-%2003/01/23.pdf?v=1675260448",
     "1fd11a1f7cb2a454524c3c39f30bc904a28403fd5ab689c7ba110c69971b431d", 661.1),
    ("2026-01-05_595pt.pdf",
     "https://cdn.www.gob.pe/uploads/document/file/9243706/"
     "7585583-reporte-de-ingreso-y-precios-en-el-gran-mercado-mayorista-"
     "de-lima-05-01-2026.pdf?v=1769791140",
     "c27248b5f1ebce92ec8f9ecff8c52e5cdc67de7d1372c7ff10691345d25a5c0a", 595.2),
]


def main() -> int:
    fallos = 0
    for nombre, url, sha, ancho in EDICIONES:
        destino = os.path.join(AQUI, nombre)
        if os.path.exists(destino):
            with open(destino, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            estado = "ya esta" if actual == sha else "HUELLA DISTINTA"
            print(f"  {estado:15s} {nombre}")
            if actual != sha:
                fallos += 1
            continue
        try:
            r = requests.get(url, timeout=60, headers=UA)
            r.raise_for_status()
        except requests.RequestException as e:
            # No se calla: un fixture que no se puede traer es una prueba que
            # deja de existir, y eso tiene que verse (Principio II).
            print(f"  FALLO           {nombre}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            fallos += 1
            continue
        actual = hashlib.sha256(r.content).hexdigest()
        if actual != sha:
            print(f"  HUELLA DISTINTA {nombre}: la fuente republico el documento.\n"
                  f"                  esperada {sha}\n"
                  f"                  obtenida {actual}", file=sys.stderr)
            fallos += 1
            continue
        with open(destino, "wb") as f:
            f.write(r.content)
        print(f"  bajado          {nombre}  {len(r.content):,} bytes  "
              f"(ancho esperado {ancho} pt)")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
