"""El orden de imports de las Lambdas es un INVARIANTE, no un detalle de estilo.

QUÉ PROTEGE
-----------
Varios módulos del pipeline leen su configuración en tiempo de import:

    embeddings.EMBED_MODEL   = os.environ.get("EMBED_MODEL", ...)
    indexer.RUTA_CACHE       = os.environ.get("PRECIOVIVO_EMBED_CACHE", ...)
    harvester.RAW_DIR        = os.environ.get("PRECIOVIVO_RAW", ...)

Las Lambdas fijan esas variables ANTES de importar el paquete. Si el orden se
invierte, la variable llega tarde y el módulo se queda con el valor por defecto.

NO ES HIPOTÉTICO. Pasó: la Lambda de indexado fijaba EMBED_MODEL dentro del
handler y acabó embebiendo con `text-embedding-3-small` usando la clave de Jina.
La firma resultante no coincidía con la del índice publicado, así que el caché no
servía de nada — y solo se detectó porque el preflight reportó "0 vectores en
caché" en vez de 9.000.

POR QUÉ UNA PRUEBA Y NO UN COMENTARIO
--------------------------------------
Antes había `# noqa: E402` en esos imports, que al menos señalaba que el orden
era deliberado. Ruff los marcó como innecesarios —E402 no está activo— y los
quitó al auto-arreglar. El invariante quedó sin nada que lo defienda: cualquier
formateador que ordene imports lo rompería en silencio y el fallo aparecería
semanas después, en producción, como una recuperación vectorial apagada.

Esta prueba no depende de la configuración del linter.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"

# Qué archivo depende de qué variable. Explícito a propósito: si mañana una
# Lambda nueva necesita el mismo trato, tiene que aparecer aquí.
CASOS = {
    "lambda_indice/indexar.py": ("PRECIOVIVO_EMBED_CACHE", "PRECIOVIVO_RAG_WEB"),
    "lambda_ingesta/ingesta.py": ("PRECIOVIVO_RAW", "PRECIOVIVO_DB"),
    "lambda_ingesta/sisap_lambda.py": ("PRECIOVIVO_RAW", "PRECIOVIVO_DB"),
    "lambda_forecast/forecast_lambda.py": ("PRECIOVIVO_DB",),
    "lambda_export/exportar.py": ("PRECIOVIVO_DB",),
}

_IMPORT_PIPELINE = re.compile(r"^\s*(from|import)\s+preciovivo\b", re.MULTILINE)


@pytest.mark.parametrize("relativo,variables", CASOS.items())
def test_la_configuracion_se_fija_antes_de_importar_el_pipeline(
        relativo: str, variables: tuple[str, ...]):
    ruta = INFRA / relativo
    if not ruta.exists():
        pytest.skip(f"{relativo} no existe en este árbol")
    fuente = ruta.read_text(encoding="utf-8")

    m = _IMPORT_PIPELINE.search(fuente)
    assert m, f"{relativo} ya no importa `preciovivo`; actualiza este guard."
    linea_import = fuente[:m.start()].count("\n")

    for var in variables:
        m_var = re.search(rf'^\s*os\.environ\.setdefault\(\s*["\']{var}["\']',
                          fuente, re.MULTILINE)
        assert m_var, (
            f"{relativo} ya no fija {var}. Si dejó de hacer falta, quítala de "
            f"CASOS; si sigue haciendo falta, el módulo se quedará con el valor "
            f"por defecto.")
        linea_var = fuente[:m_var.start()].count("\n")
        assert linea_var < linea_import, (
            f"{relativo}: {var} se fija en la línea {linea_var + 1}, DESPUÉS de "
            f"importar `preciovivo` en la {linea_import + 1}.\n"
            f"Los módulos del pipeline leen su configuración en tiempo de "
            f"import: fijarla después no tiene efecto y el módulo se queda con "
            f"el valor por defecto, sin ningún error visible.")
