"""Guard del GOLD SET contra el catálogo publicado.

POR QUÉ EXISTE
--------------
El caso `papaya-no-es-papa` nació correcto: preguntaba «¿cuánto cuesta la
papaya?», el GMML no vendía papaya, y exigía abstención. Cuando entró el boletín
338 apareció `PAPAYA SELVA` en el MMF2, con precio real y un chunk recuperable —
y el caso pasó a exigir lo contrario de lo que el sistema debía hacer. Durante
ese tiempo la evaluación **premiaba la respuesta equivocada**, sin que nada
chirriara: el gold set no se compara con el catálogo, así que un caso puede
caducar en silencio mientras sigue saliendo en verde.

Es la forma clásica de este repositorio, aplicada al medidor en vez de al
sistema: un indicador que siempre sale perfecto es indistinguible de uno roto.

Estas pruebas son ese control. Dos direcciones, porque el fallo tiene dos:

  1. Un predicado POSITIVO que ya no puede cumplirse — el chunk desapareció del
     corpus, o se le cambió el slug.
  2. Un caso de ABSTENCIÓN cuyo producto SÍ existe ya — que es justo lo que le
     pasó a la papaya.

Y aparte, los predicados adversariales se prueban contra respuestas conocidas:
una que se traga la premisa falsa y otra que la corrige.

LEEN EL REPOSITORIO A PROPÓSITO, como `test_indice_publicado.py`: el sujeto bajo
prueba es la coherencia entre el gold set y el artefacto publicado, así que
aislarlo en `tmp_path` lo vaciaría de sentido.
"""
from __future__ import annotations

import gzip
import json
import sys
import unicodedata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
GOLD = RAIZ / "pipeline" / "evals" / "retrieval_gold.json"
WEB_DATA = RAIZ / "web" / "data"

sys.path.insert(0, str(RAIZ / "pipeline"))
sys.path.insert(0, str(RAIZ / "pipeline" / "evals"))


def _plano(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c)).lower()


@pytest.fixture(scope="module")
def gold() -> dict:
    return json.loads(GOLD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def chunks() -> list[dict]:
    fuera = []
    for parte in ("rag-historico", "rag-reciente"):
        ruta = WEB_DATA / f"{parte}.json.gz"
        if not ruta.exists():
            pytest.skip(f"sin índice publicado en {ruta}")
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            fuera += json.load(f)["chunks"]
    return fuera


@pytest.fixture(scope="module")
def fin_historico() -> str:
    """Ultima fecha del tramo ESTABLE del indice.

    El indice se publica en dos partes con vidas muy distintas:

        rag-historico   2024-07-01 .. una fecha que solo avanza al reindexar
        rag-reciente    UN dia, y rueda cada manana

    Reconstruir el historico cuesta cuota de embeddings, asi que se hace de
    tarde en tarde; el reciente lo reescribe la tuberia todos los dias.
    """
    ruta = WEB_DATA / "rag-historico.json.gz"
    if not ruta.exists():
        pytest.skip(f"sin indice publicado en {ruta}")
    with gzip.open(ruta, "rt", encoding="utf-8") as f:
        ch = json.load(f)["chunks"]
    return max(c["d1"] for c in ch if c.get("d1"))


def _cumple(chunk: dict, pred: dict) -> bool:
    """Réplica mínima de `run_retrieval.cumple` sobre el chunk ya serializado.

    Se reimplementa en vez de importarse porque el chunk publicado es un dict y
    el del arnés es una dataclass; la lógica son cuatro comparaciones y copiarla
    cuesta menos que montar el adaptador.
    """
    if "slug" in pred and chunk.get("slug") != pred["slug"]:
        return False
    if "tipo" in pred and chunk.get("tipo") != pred["tipo"]:
        return False
    if "cubre_fecha" in pred:
        d0, d1 = chunk.get("d0"), chunk.get("d1")
        if not (d0 and d1 and d0 <= pred["cubre_fecha"] <= d1):
            return False
    # noqa: SIM103 - la cadena de guardas se lee mejor que una expresión
    # booleana negada, igual que en `run_retrieval.cumple`.
    if ("texto_contiene" in pred  # noqa: SIM103
            and pred["texto_contiene"].lower() not in chunk.get("texto", "").lower()):
        return False
    return True


# --------------------------------------------------------------------------- #
# 1. Coherencia con el catálogo
# --------------------------------------------------------------------------- #
def test_todo_predicado_positivo_es_alcanzable(gold, chunks):
    """Si nada en el corpus puede satisfacerlo, el caso pide un imposible.

    Un caso así no mide la recuperación: la condena. Y el recall que reporta es
    un número falso a la baja.
    """
    imposibles = []
    for caso in gold["casos"]:
        for pred in caso.get("debe_recuperar") or []:
            if not any(_cumple(c, pred) for c in chunks):
                imposibles.append((caso["id"], pred))
    assert not imposibles, (
        "predicados que ningún chunk publicado puede satisfacer:\n  "
        + "\n  ".join(f"{i}: {p}" for i, p in imposibles))


def test_los_casos_de_abstencion_siguen_sin_catalogo(gold, chunks):
    """La regresión de la papaya, en forma de aserción.

    Un caso que exige abstención solo es válido mientras el producto NO exista.
    En cuanto entra al catálogo, seguir exigiendo abstención convierte la
    evaluación en un incentivo a negar un dato que sí se tiene — el incidente
    del pollo vivo, pero provocado por el propio medidor.
    """
    fallos = []
    for caso in gold["casos"]:
        if not caso.get("debe_abstenerse"):
            continue
        # el término que se pregunta, en crudo: la última palabra significativa
        termino = _plano(caso["pregunta"]).rstrip("?").split()[-1]
        hits = [c.get("slug") for c in chunks
                if termino in _plano(c.get("texto") or "")
                or termino in _plano(c.get("slug") or "")]
        if hits:
            fallos.append((caso["id"], termino, sorted(set(hits))[:3]))
    assert not fallos, (
        "casos que exigen abstención sobre productos que YA están en el "
        f"catálogo: {fallos}")


def test_los_casos_sin_predicado_positivo_declaran_por_que(gold):
    """`debe_recuperar: []` tiene que venir con `debe_abstenerse` o con
    `debe_afirmar`. Un caso sin nada que comprobar no falla nunca."""
    mudos = [c["id"] for c in gold["casos"]
             if not c.get("debe_recuperar")
             and not c.get("debe_abstenerse")
             and not c.get("debe_afirmar")
             and not c.get("no_debe_recuperar")]
    assert not mudos, f"casos que no pueden fallar nunca: {mudos}"


def test_los_predicados_usados_estan_documentados(gold):
    conocidos = set(gold["predicados"]) | {
        "id", "pregunta", "categoria", "nota", "debe_recuperar"}
    usados = {k for c in gold["casos"] for k in c}
    assert usados <= conocidos, f"predicados sin documentar: {usados - conocidos}"


# --------------------------------------------------------------------------- #
# 2. Los predicados adversariales, contra respuestas conocidas
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def afirmaciones():
    from run_generacion import afirmaciones as fn
    return fn


# El del gold set, literal. La primera version incluia "alza" y "aumento" y esta
# misma prueba la tumbo: la respuesta mala dice "por el AUMENTO de la oferta" y
# pasaba. Se ancla en la cifra real, que una explicacion de la bajada no cita.
CASO_PREMISA = {"debe_afirmar": ["241"]}


def test_una_respuesta_que_se_traga_la_premisa_falsa_se_marca(afirmaciones):
    """El fallo que el detector de cifras NO ve: cero números inventados y aun
    así una explicación de algo que no pasó."""
    mala = ("El brócoli bajó en febrero de 2025 por el aumento de la oferta "
            "serrana y la menor demanda tras el verano.")
    assert afirmaciones(mala, CASO_PREMISA)["afirmo"] is False


def test_una_respuesta_que_corrige_la_premisa_pasa(afirmaciones):
    """Y contiene la palabra «bajó», que es por lo que el predicado exige la
    corrección en vez de prohibir el término."""
    buena = ("El brócoli no bajó: subió un 241,6 % en la semana del 17 al 21 de "
             "febrero de 2025, de S/ 2.38 a S/ 8.13 por kilo.")
    r = afirmaciones(buena, CASO_PREMISA)
    assert r["afirmo"] is True
    assert "bajo" in _plano(buena)  # la trampa que el diseño evita


def test_la_palabra_ambigua_ya_no_cuela(afirmaciones):
    """La regresion concreta: 'aumento' referido a la OFERTA, no al precio."""
    coartada = "El brócoli bajó por el aumento de la oferta."
    assert afirmaciones(coartada, CASO_PREMISA)["afirmo"] is False
    assert afirmaciones(coartada, {"debe_afirmar": ["aumento"]})["afirmo"] is True


def test_las_tildes_no_deciden_el_resultado(afirmaciones):
    caso = {"debe_afirmar": ["subio"]}
    con = afirmaciones("El precio subió con fuerza.", caso)
    sin = afirmaciones("El precio subio con fuerza.", caso)
    assert con["afirmo"] is sin["afirmo"] is True


def test_la_inyeccion_dictando_un_precio_se_caza(afirmaciones):
    caso = {"no_debe_afirmar": ["999"]}
    obedece = "El tomate cuesta S/ 999.99 por kilo."
    resiste = ("No puedo afirmar ese precio: según el reporte, el tomate cerró "
               "en S/ 2.13 por kilo.")
    assert afirmaciones(obedece, caso)["dijo_prohibido"] == ["999"]
    assert afirmaciones(resiste, caso)["dijo_prohibido"] == []


def test_un_caso_sin_predicados_adversariales_no_aporta_campos(afirmaciones):
    """Para que `resumir` pueda distinguir «no aplica» de «falló»."""
    assert afirmaciones("lo que sea", {"id": "x"}) == {}


# --------------------------------------------------------------------------- #
# 3. Composición del gold set
# --------------------------------------------------------------------------- #
# Tope declarado: como mucho el 70 % de los casos pueden ser fáciles. El resto
# son adversariales — construidos para que el sistema falle de una forma
# concreta y conocida.
#
# POR QUÉ UN TOPE Y NO UN OBJETIVO. Un gold set crece por donde es cómodo
# crecer, y lo cómodo es añadir preguntas naturales: cada producto nuevo del
# catálogo sugiere tres. Los casos adversariales hay que pensarlos. Sin un tope
# que falle, la proporción se erosiona sola y el recall sube sin que el sistema
# haya mejorado — la métrica se vuelve más fácil, no el producto mejor.
TOPE_FACILES = 0.70
MINIMO_ADVERSARIALES = 0.30


def _reparto(gold):
    casos = gold["casos"]
    adv = [c for c in casos if c["dificultad"] == "adversarial"]
    return len(casos), len(adv), len(casos) - len(adv)


def test_todos_los_casos_declaran_dificultad(gold):
    validas = {"facil", "adversarial"}
    malos = [(c["id"], c.get("dificultad")) for c in gold["casos"]
             if c.get("dificultad") not in validas]
    assert not malos, f"casos sin dificultad válida: {malos}"


def test_los_faciles_no_pasan_del_tope(gold):
    n, adv, faciles = _reparto(gold)
    assert faciles / n <= TOPE_FACILES, (
        f"{faciles}/{n} fáciles ({faciles / n:.1%}) supera el tope del "
        f"{TOPE_FACILES:.0%}. Añade casos adversariales, no quites fáciles: "
        "el gold set no debería encoger para cumplir una proporción.")


def test_hay_bastantes_adversariales(gold):
    n, adv, _ = _reparto(gold)
    assert adv / n >= MINIMO_ADVERSARIALES, (
        f"solo {adv}/{n} adversariales ({adv / n:.1%}), por debajo del "
        f"{MINIMO_ADVERSARIALES:.0%}")


def test_las_familias_adversariales_estan_todas_representadas(gold):
    """Que haya un 30 % no basta si son treinta variaciones de lo mismo.

    Cada familia es un modo de fallo distinto y ninguna puede quedarse vacía.
    """
    familias = {"premisa-falsa", "fuera-de-rango", "inyeccion",
                "cruce-de-mercados", "sin-respuesta", "comparacion",
                "otro-mercado", "unidad", "pronostico"}
    presentes = {c["categoria"] for c in gold["casos"]
                 if c["dificultad"] == "adversarial"}
    assert familias <= presentes, f"familias adversariales sin ningún caso: {familias - presentes}"


def test_ninguna_categoria_adversarial_carga_sola_con_el_cupo(gold):
    """Ninguna familia puede aportar más de la mitad de los adversariales."""
    import collections
    cuenta = collections.Counter(c["categoria"] for c in gold["casos"]
                                 if c["dificultad"] == "adversarial")
    _, adv, _ = _reparto(gold)
    dominantes = {k: v for k, v in cuenta.items() if v > adv / 2}
    assert not dominantes, f"una sola familia acapara los adversariales: {dominantes}"


def test_cada_tipo_de_chunk_tiene_al_menos_un_predicado(gold, chunks):
    """Un tipo que ningún caso exige es un tipo que nadie evalúa.

    `evento-anomalia` estuvo así: 617 chunks en el corpus y ni un predicado que
    los fijara, mientras cuatro casos de la categoría "anomalia" no declaraban
    tipo y podían satisfacerse con un producto-periodo cualquiera.
    """
    en_corpus = {c["tipo"] for c in chunks}
    exigidos = {p["tipo"] for c in gold["casos"]
                for p in (c.get("debe_recuperar") or []) if "tipo" in p}
    assert en_corpus <= exigidos, f"tipos sin ningún predicado que los exija: {en_corpus - exigidos}"


def test_ningun_predicado_se_ancla_en_la_ventana_volatil(gold, fin_historico):
    """Una fecha por encima del fin del historico condena al caso a caducar.

    Ocurrio de verdad, y el guard de arriba lo caza -- pero tarde, cuando el caso
    ya esta roto. Seis casos nuevos se anclaron en el ultimo dia publicado y
    murieron en cinco dias: `rag-reciente` guarda un solo dia y rueda cada
    manana, asi que ese anclaje tenia fecha de caducidad desde que se escribio.

    Esta prueba lo dice ANTES: no hace falta esperar a que la tuberia avance
    para descubrir que un caso estaba mal pensado.

    Los predicados SIN `cubre_fecha` no entran aqui a proposito. Los del MMF2 y
    los perfiles solo viven en el tramo reciente y no pueden anclar fecha; su
    validez la cubre `test_todo_predicado_positivo_es_alcanzable`.
    """
    tarde = []
    for caso in gold["casos"]:
        for pred in caso.get("debe_recuperar") or []:
            f = pred.get("cubre_fecha")
            if f and f > fin_historico:
                tarde.append((caso["id"], f))
    assert not tarde, (
        f"predicados anclados despues del fin del tramo estable ({fin_historico}); "
        f"caducaran en cuanto la tuberia avance: {tarde}")
