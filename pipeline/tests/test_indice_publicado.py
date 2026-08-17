"""Guard de integridad del ÍNDICE PUBLICADO (`web/data/rag-*`).

POR QUÉ EXISTE
-------------
Toda la disciplina de verificación del proyecto apuntaba al DATO —el
dead-man's-switch vigila la frescura del snapshot, las evals ponen umbral al
recall— y ninguna apuntaba al ARTEFACTO que el sitio carga. Resultado: se
publicó un índice construido con el embebedor local, sin la parte histórica, y
estuvo así sin que nada se quejara.

Ese fallo es invisible por diseño. `web/lib/rag.ts` embebe la consulta con
`api:<modelo>:<dims>` por HTTP (Vercel no puede correr model2vec), compara esa
firma con la del índice, no coincide, lanza — y `recuperar()` lo captura y
devuelve solo el piso determinista. Como el piso SÍ trae chunks, la respuesta se
sirve igual y se etiqueta `llm-rag`. El sitio responde 200, con datos reales,
diciendo que hizo RAG. Nunca hubo recuperación vectorial.

`indexer.py` ya lo advertía con un `print`. No alcanzó: un aviso que se puede
ignorar no es un control. Estas tres aserciones son el control.

ESTOS TESTS LEEN EL REPOSITORIO A PROPÓSITO. Es lo contrario del resto de la
suite, que se aísla en `tmp_path`: aquí el sujeto bajo prueba ES el estado
publicado del repo, así que aislarlo lo vaciaría de sentido.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.publicado

RAIZ = Path(__file__).resolve().parents[2]
WEB_DATA = RAIZ / "web" / "data"
README = RAIZ / "README.md"
PARTES = ("rag-historico", "rag-reciente")

RECONSTRUIR = (
    "Reconstruye y publica con:\n"
    "    cd pipeline\n"
    "    EMBED_API_KEY=... python -m preciovivo.ingest --index\n"
    "(sin --index-solo-reciente: hay que reescribir TAMBIÉN el histórico)"
)


def firma_de_produccion() -> str:
    """La firma que `web/lib/rag.ts` construirá en tiempo de request.

    Se deriva de las MISMAS variables (`EMBED_MODEL`, `EMBED_DIMS`) y los mismos
    valores por defecto que usa el sitio, para que este test y producción no
    puedan discrepar.
    """
    from preciovivo.embeddings import EMBED_DIMS, EMBED_MODEL

    return f"api:{EMBED_MODEL}:{EMBED_DIMS}"


def meta_de(parte: str) -> dict:
    with gzip.open(WEB_DATA / f"{parte}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)["meta"]


def chunks_declarados_en_readme() -> int:
    """El total que el README publica, p. ej. `**9 206 chunks.**`."""
    m = re.search(r"^\*\*([\d ]+) chunks\.\*\*$", README.read_text(encoding="utf-8"),
                  re.MULTILINE)
    assert m, ("No encontré la línea `**N chunks.**` en el README. Es el número "
               "que este guard contrasta contra el artefacto; si cambia de "
               "formato, actualiza también esta expresión.")
    return int(m.group(1).replace(" ", ""))


# --------------------------------------------------------------------------- #
# 1) Las cuatro partes existen
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("parte", PARTES)
@pytest.mark.parametrize("ext", (".bin", ".json.gz"))
def test_existe_cada_parte_del_indice(parte: str, ext: str):
    ruta = WEB_DATA / f"{parte}{ext}"
    assert ruta.exists(), (
        f"Falta {ruta.relative_to(RAIZ)}.\n"
        f"Sin la parte histórica el sitio solo puede recuperar la ventana en "
        f"curso: se pierden los ~2 años de profundidad temporal que son la razón "
        f"de ser del RAG.\n{RECONSTRUIR}")
    assert ruta.stat().st_size > 0, f"{ruta.relative_to(RAIZ)} está vacío."


# --------------------------------------------------------------------------- #
# 2) La firma es la que el sitio va a usar
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("parte", PARTES)
def test_firma_del_indice_es_la_de_produccion(parte: str):
    if not (WEB_DATA / f"{parte}.json.gz").exists():
        pytest.skip("cubierto por test_existe_cada_parte_del_indice")
    firma = meta_de(parte)["firma_embedder"]
    esperada = firma_de_produccion()
    assert firma == esperada, (
        f"'{parte}' se construyó con '{firma}' y el sitio consultará con "
        f"'{esperada}'.\n"
        f"No coincidir NO da un error visible: `rag.ts` captura el fallo, cae al "
        f"piso determinista y sigue etiquetando la respuesta como 'llm-rag'. La "
        f"recuperación vectorial queda apagada sin que nadie se entere.\n"
        f"{RECONSTRUIR}")


def test_ambas_partes_comparten_embebedor():
    """Dos partes con embebedores distintos son peor que una parte mal: sus
    vectores no viven en el mismo espacio y el coseno los mezcla sin quejarse."""
    if not all((WEB_DATA / f"{p}.json.gz").exists() for p in PARTES):
        pytest.skip("cubierto por test_existe_cada_parte_del_indice")
    firmas = {p: meta_de(p)["firma_embedder"] for p in PARTES}
    assert len(set(firmas.values())) == 1, f"partes con embebedores distintos: {firmas}"


# --------------------------------------------------------------------------- #
# 3) El conteo publicado es el que el README afirma
# --------------------------------------------------------------------------- #
def test_conteo_de_chunks_coincide_con_el_readme():
    """El README no puede afirmar un tamaño de corpus que el artefacto no tiene.

    Es la regla de "nada se afirma sin calcularse" aplicada a la documentación:
    el número queda atado al archivo, así que una reconstrucción que cambie el
    corpus obliga a actualizar el README en el mismo commit.
    """
    if not all((WEB_DATA / f"{p}.json.gz").exists() for p in PARTES):
        pytest.skip("cubierto por test_existe_cada_parte_del_indice")

    por_parte = {p: meta_de(p)["n_chunks"] for p in PARTES}
    real = sum(por_parte.values())
    declarado = chunks_declarados_en_readme()
    desglose = ", ".join(f"{p}={n:,}" for p, n in por_parte.items())
    assert real == declarado, (
        f"El README declara {declarado:,} chunks y el índice publicado tiene "
        f"{real:,} ({desglose}).\n"
        f"Si la reconstrucción es correcta, el que hay que corregir es el README.")


def test_el_bin_y_el_json_describen_el_mismo_numero_de_chunks():
    """El .bin es un blob plano sin cabecera: si su tamaño no cuadra con
    n_chunks x dims, las filas se leerían corridas y cada chunk quedaría
    emparejado con el vector de otro. Silencioso y catastrófico."""
    for parte in PARTES:
        if not (WEB_DATA / f"{parte}.json.gz").exists():
            pytest.skip("cubierto por test_existe_cada_parte_del_indice")
        meta = meta_de(parte)
        esperado = meta["n_chunks"] * meta["dims"]  # int8 -> 1 byte por componente
        real = (WEB_DATA / f"{parte}.bin").stat().st_size
        assert real == esperado, (
            f"{parte}.bin mide {real:,} bytes y n_chunks x dims = "
            f"{meta['n_chunks']:,} x {meta['dims']} = {esperado:,}.")
