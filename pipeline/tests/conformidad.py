"""Genera el CONTRATO DE CONFORMIDAD entre `retrieval.py` y `web/lib/rag.ts`.

EL PROBLEMA QUE RESUELVE
------------------------
El sitio reimplementa en TypeScript el parseo de la pregunta y BM25 porque no
tiene a quién preguntarle. Son dos implementaciones de las mismas reglas, y la
única garantía de que no divergen era CONVENCIONAL: que alguien se acordara de
espejar cada test a mano. Una convención no detecta lo que nadie escribió.

Esto la vuelve una garantía por construcción. Python —que es la implementación
de referencia, la que usan las evaluaciones y la CLI— produce las salidas
esperadas para un conjunto de casos, y el sitio tiene que reproducirlas
exactamente. Si se toca `retrieval.py`, el fixture cambia y
`web/test/conformidad.test.ts` falla hasta que `rag.ts` lo alcance. Si se toca
`rag.ts` y se desvía, falla igual.

Se comparan ORDEN e IDs, no scores crudos: ambos lenguajes usan float64, pero el
orden de acumulación difiere y dos scores separados por 1e-16 no significan
nada. El desempate por id —que las dos implementaciones aplican— es lo que hace
el orden total y comparable.

Regenerar tras un cambio deliberado:
    cd pipeline && python tests/conformidad.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE))

from preciovivo import retrieval as R  # noqa: E402

DESTINO = PIPELINE.parent / "web" / "test" / "fixtures" / "conformidad.json"

# Fecha del último dato. Las expresiones temporales se resuelven contra ESTO y
# no contra el reloj, así que el fixture es estable en el tiempo.
FECHA_REF = "2026-08-14"

# Catálogo recortado pero con las trampas reales del dominio:
#   - varias variedades que comparten primera palabra ("papa" son diez)
#   - "Papaya", que contiene "papa" como subcadena pero NO como token
#   - "Ajo Criollo O Napuri", con un conector que nadie escribe
#   - "Maiz Choclo Serrano", cuyo último token es una palabra común
CATALOGO = {
    "papa-blanca": "Papa Blanca",
    "papa-amarilla": "Papa Amarilla",
    "papa-canchan": "Papa Canchan",
    "papaya": "Papaya",
    "ajo-criollo-o-napuri": "Ajo Criollo O Napuri",
    "ajo-morado": "Ajo Morado",
    "tomate": "Tomate",
    "zanahoria": "Zanahoria",
    "maiz-choclo-serrano": "Maiz Choclo Serrano",
    "limon-sutil-cajon": "Limon Sutil Cajon",
    # Los tres nombres del catálogo REAL que ejercitan el filtro de tokens de
    # `_tokens_significativos`. Sin ellos el contrato no cubría esa regla:
    # comprobado quitando "precio" de _PALABRAS_VACIAS en el sitio, y los 92
    # casos seguían pasando. Una regla que ningún caso toca no está contratada.
    "manzana-cte-para-agua": "Manzana Cte/Para Agua",      # "para" es palabra vacía
    "maiz-marlo-coronta-de-maiz": "Maiz Marlo/Coronta De Maiz",  # "de" mide 2 letras
}

PREGUNTAS = [
    # producto + tiempo
    "¿por qué subió la papa esta semana?",
    "¿cuánto costó el tomate el 15 de julio?",
    "¿qué pasó con la zanahoria en julio?",
    "precio del tomate ayer",
    "papa blanca hoy",
    "zanahoria en los ultimos 30 dias",
    "tomate la semana pasada",
    "tomate el mes pasado",
    "zanahoria este mes",
    "papa el año pasado",
    "tomate este año",
    "tomate en 2026-03-11",
    # desambiguación y agrupación
    "compara ajo morado con ajo criollo",
    "¿a cuánto está la papa?",
    "¿y la papaya?",
    "napuri",
    "maiz choclo serrano",
    # estacional (el mes NO debe acotar el rango)
    "¿cuánto suele costar la zanahoria en agosto?",
    "¿el tomate normalmente sube en julio?",
    "precio historico del limon",
    # agregadas, sin producto
    "¿qué está más barato hoy?",
    "¿hubo algo raro esta semana?",
    "¿qué tubérculos subieron?",
    # tokens que el filtro de `_tokens_significativos` debe descartar del NOMBRE
    "manzana para agua",              # "para" no cuenta: identifican manzana + agua
    "¿qué sirve para el jugo?",       # "para" sola NO debe matchear la manzana
    "maiz marlo",                     # "de" no cuenta
    "¿a cuánto está el maiz?",        # agrupa la familia por la cabeza "maiz"
    "napuri o criollo",               # "o" mide 2 letras: se descarta
    # bordes
    "",
    "xyzzy qwerty",
    "el 31 de febrero",
]

# Corpus mínimo y explícito para BM25: va DENTRO del fixture para que ambos
# lados indexen exactamente el mismo texto, sin depender de `build_corpus`.
CORPUS = [
    ("producto-periodo:papa-blanca:2026-W32",
     "Papa Blanca · semana 2026-W32 · GMML.\n"
     "Precio: abrió en S/ 2.00/kg el 3 de agosto y cerró en S/ 2.40/kg el 7 de agosto (+20.0% en el periodo)."),
    ("producto-periodo:papa-amarilla:2026-W32",
     "Papa Amarilla · semana 2026-W32 · GMML.\n"
     "Precio: abrió en S/ 3.50/kg el 3 de agosto y cerró en S/ 3.60/kg el 7 de agosto (+2.9% en el periodo)."),
    ("producto-perfil:papaya",
     "Ficha de Papaya (papaya) · GMML.\n"
     "Precio histórico: mínimo S/ 1.80, máximo S/ 5.20, promedio S/ 3.10 por kg."),
    ("evento-anomalia:tomate:2026-08-05:precio",
     "Anomalía de precio · Tomate · miércoles 5 de agosto de 2026 · GMML.\n"
     "El valor observado (precio S/ 6.20/kg) se apartó 4.1 desviaciones robustas de su historia reciente."),
    ("mercado-dia:2026-08-07",
     "GMML · viernes 7 de agosto de 2026.\n"
     "72 productos con precio publicado. Lo más barato del día: Papa Yungay (S/ 0.88/kg)."),
    ("producto-periodo:zanahoria:2026-W32",
     "Zanahoria · semana 2026-W32 · GMML.\n"
     "Precio: abrió en S/ 0.80/kg y cerró en S/ 0.87/kg. Tendencia reportada por la fuente: En Alza (3 días)."),
]

CONSULTAS_BM25 = [
    "papa",
    "papaya",
    "precio",
    "anomalía de precio en tomate",
    "semana 2026-W32",
    "xyzzyqwerty",
]

CASOS_RRF = [
    [["a", "b", "c"], ["b", "a", "c"]],
    [["a"], ["a"]],
    [["a"], ["b"]],
    [[], []],
    [["a", "b", "c"]],
    [["x", "y"], ["y", "z"], ["z", "x"]],
]


def construir() -> dict:
    from preciovivo.corpus import Chunk

    parseo = []
    for q in PREGUNTAS:
        desde, hasta = R.detectar_rango_fechas(q, FECHA_REF)
        tipos = R.detectar_tipos(q)
        parseo.append({
            "pregunta": q,
            "tokens": R.tokenizar(q),
            "slugs": sorted(R.detectar_productos(q, CATALOGO)),
            "desde": desde,
            "hasta": hasta,
            "estacional": R.es_estacional(q),
            # `tipos` no está portado al sitio; se registra igual para que el día
            # que se porte, el contrato ya exista.
            "tipos": sorted(tipos) if tipos else None,
        })

    chunks = [
        Chunk(id=i, tipo=i.split(":")[0], texto=t, slug=None, producto=None,
              mercado="GMML", fecha_inicio="2026-08-03", fecha_fin="2026-08-07")
        for i, t in CORPUS
    ]
    bm = R.BM25(chunks)
    bm25 = [
        {"pregunta": q,
         "ids": [chunks[i].id for i, _ in bm.buscar(q, k=len(chunks))]}
        for q in CONSULTAS_BM25
    ]

    rrf = []
    for listas in CASOS_RRF:
        fusion = R.rrf(listas)
        orden = sorted(fusion.items(), key=lambda kv: (-kv[1], kv[0]))
        rrf.append({
            "listas": listas,
            "orden": [cid for cid, _ in orden],
            # 9 decimales: muy por encima del ruido de float64 y muy por debajo
            # de cualquier diferencia con significado.
            "scores": {cid: round(s, 9) for cid, s in fusion.items()},
        })

    return {
        "_generado_por": "pipeline/tests/conformidad.py",
        "_por_que": ("Contrato entre retrieval.py (referencia) y web/lib/rag.ts. "
                     "Regenerar: cd pipeline && python tests/conformidad.py"),
        "fecha_ref": FECHA_REF,
        "catalogo": CATALOGO,
        "rrf_c": R.RRF_C,
        "parseo": parseo,
        "corpus": [{"id": i, "texto": t} for i, t in CORPUS],
        "bm25": bm25,
        "rrf": rrf,
    }


def escribir() -> Path:
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps(construir(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return DESTINO


if __name__ == "__main__":
    ruta = escribir()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    print(f"escrito {ruta}")
    print(f"  {len(datos['parseo'])} casos de parseo · "
          f"{len(datos['bm25'])} de BM25 · {len(datos['rrf'])} de RRF")
