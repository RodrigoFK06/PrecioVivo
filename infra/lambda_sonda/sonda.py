"""Sonda de alcanzabilidad: ¿deja el WAF de gob.pe que una Lambda coseche?

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
`harvester.py` dice en su docstring "Runs locally (the gob.pe WAF does not block
this machine)" y `actualizar-diario.ps1` avisa de que "un runner de datacenter
podria estar bloqueado por el WAF". gob.pe sirve detrás de Huawei Cloud WAF
(cookie HWWAFSESID). Lambda sale por IP de datacenter de AWS.

Eso es un go/no-go de la Fase 3 entera y NO se puede responder desde una
máquina de casa. Esta función se despliega, se invoca una vez y se destruye.

QUÉ SE MIDE, Y POR QUÉ ASÍ
--------------------------
No se comprueba "status 200". Un WAF contesta 200 con una página de desafío de
JavaScript encantado de la vida: el código diría que funciona y no funcionaría.
Lo que se comprueba es que el CONTENIDO sirve para cosechar, recorriendo la
cadena real del harvester con el harvester real, sin reimplementar nada:

  1. IP de salida            (checkip.amazonaws.com, servicio de AWS)
  2. month_pages()           la colección 335 parsea a páginas de mes
  3. latest_dailies(2)       de la página de mes salen URLs de PDF con fecha
  4. download()              el CDN entrega un PDF de verdad (magic %PDF-)

El paso 4 importa aparte: cdn.www.gob.pe es otro origen y puede no estar detrás
del mismo WAF. Se puede dar el caso de que el PDF se descargue pero la página
que lo lista esté bloqueada — y ahí no hay cosecha, porque las URLs no se
adivinan.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _paso(nombre, fn):
    """Ejecuta un paso y devuelve su resultado sin abortar la sonda.

    Si el paso 2 falla queremos igualmente el resultado del 4: "la página está
    bloqueada pero el CDN no" y "todo bloqueado" son diagnósticos distintos que
    llevan a soluciones distintas.
    """
    t = time.perf_counter()
    try:
        datos = fn()
        return {"paso": nombre, "ok": True, "ms": round((time.perf_counter() - t) * 1000), **datos}
    except Exception as e:  # noqa: BLE001
        return {"paso": nombre, "ok": False, "ms": round((time.perf_counter() - t) * 1000),
                "error": f"{type(e).__name__}: {e}",
                "traza": traceback.format_exc()[-600:]}


def _ip():
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as r:
        return {"ip": r.read().decode().strip()}


def handler(_event, _context):
    import harvester as H  # copia literal de pipeline/preciovivo/harvester.py

    resultados = [_paso("ip-de-salida", _ip)]

    def paso_coleccion():
        html = H._get(H.COLLECTION_URL)
        meses = H.month_pages()
        # La huella del WAF: una página de desafío es corta y no trae enlaces.
        return {"bytes_html": len(html), "paginas_de_mes": len(meses),
                "primera": meses[0] if meses else None,
                "muestra": html[:200].replace("\n", " ") if len(meses) == 0 else None}

    resultados.append(_paso("coleccion-335", paso_coleccion))

    def paso_pdfs():
        ds = H.latest_dailies(2)
        return {"pdfs_listados": len(ds),
                "fechas": [d.fecha.isoformat() for d in ds],
                "url": ds[-1].url if ds else None}

    resultados.append(_paso("listar-pdfs", paso_pdfs))

    def paso_descarga():
        ds = H.latest_dailies(1)
        if not ds:
            raise RuntimeError("no hay PDF que descargar (falló el paso anterior)")
        ruta, sha, n = H.download(ds[0])
        return {"fecha": ds[0].fecha.isoformat(), "bytes": n, "sha256": sha[:16]}

    resultados.append(_paso("descargar-pdf-del-cdn", paso_descarga))

    veredicto = "COSECHA VIABLE DESDE LAMBDA" if all(r["ok"] for r in resultados) else "BLOQUEADO O PARCIAL"
    salida = {"veredicto": veredicto, "pasos": resultados}
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return salida
