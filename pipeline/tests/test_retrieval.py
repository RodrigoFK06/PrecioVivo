"""Pruebas de la recuperación híbrida (preciovivo.retrieval).

Auto-contenidas: FakeEmbedder, sin red ni claves.

El grueso está en el PARSEO de la pregunta y en el PISO DETERMINISTA, que son
las dos piezas de las que depende que el asistente no falle lo obvio. La calidad
del ranking semántico no se testea aquí —para eso están las evaluaciones con un
embebedor real—; aquí se verifica la mecánica y las garantías.
"""
from __future__ import annotations

import pytest

from preciovivo import retrieval as R
from preciovivo.corpus import (
    TIPO_EVENTO_ANOMALIA,
    TIPO_MERCADO_DIA,
    TIPO_PRODUCTO_PERFIL,
    TIPO_PRODUCTO_PERIODO,
    build_corpus,
)


@pytest.fixture
def catalogo(snapshot_sintetico):
    return {p["slug"]: p["nombre"] for p in snapshot_sintetico["productos"]}


@pytest.fixture
def recuperador(snapshot_sintetico, embedder_fake):
    return R.Recuperador.desde_snapshot(snapshot_sintetico, embedder=embedder_fake)


# --------------------------------------------------------------------------- #
# Detección de productos
# --------------------------------------------------------------------------- #
def test_nombre_completo(catalogo):
    assert R.detectar_productos("¿cuánto está la papa blanca?", catalogo) == \
        frozenset({"papa-blanca"})


def test_primera_palabra_agrupa_variedades(catalogo):
    """Quien pregunta "¿por qué subió la papa?" se refiere a todas sus variedades."""
    r = R.detectar_productos("¿por qué subió la papa?", catalogo)
    assert r == frozenset({"papa-blanca", "papa-amarilla"})


def test_papa_no_matchea_papaya(catalogo):
    """Sin frontera de palabra, "papa" matchearía dentro de "papaya" y la
    respuesta mezclaría dos productos distintos."""
    assert "papaya" not in R.detectar_productos("precio de la papa", catalogo)
    assert R.detectar_productos("precio de la papaya", catalogo) == \
        frozenset({"papaya"})


def test_sin_acentos_ni_mayusculas(catalogo):
    assert R.detectar_productos("PRECIO DEL TOMATE", catalogo) == frozenset({"tomate"})


def test_por_slug(catalogo):
    assert R.detectar_productos("dame papa amarilla", catalogo) == \
        frozenset({"papa-amarilla"})


def test_sin_producto(catalogo):
    assert R.detectar_productos("¿qué está más barato hoy?", catalogo) == frozenset()


def test_varios_productos(catalogo):
    r = R.detectar_productos("compara el tomate con la papaya", catalogo)
    assert r == frozenset({"tomate", "papaya"})


def test_variedad_explicita_no_arrastra_a_las_demas(catalogo):
    """Si se nombra la variedad, no se expande a la familia: 'papa blanca' llega
    a dos tokens y 'papa amarilla' se queda en uno."""
    assert R.detectar_productos("¿cuánto está la papa blanca?", catalogo) == \
        frozenset({"papa-blanca"})


def test_dos_variedades_de_la_misma_familia(catalogo):
    """El caso que rompía el matching por nombre completo: nombrar dos hermanos
    de la misma familia tiene que traer los dos, no solo el primero."""
    r = R.detectar_productos("compara la papa blanca con la papa amarilla", catalogo)
    assert r == frozenset({"papa-blanca", "papa-amarilla"})


def test_nombre_canonico_parcial():
    """Nadie escribe "Ajo Criollo O Napuri" entero: con "ajo criollo" alcanza."""
    catalogo = {"ajo-morado": "Ajo Morado",
                "ajo-criollo-o-napuri": "Ajo Criollo O Napuri"}
    assert R.detectar_productos("diferencia entre ajo morado y ajo criollo",
                                catalogo) == frozenset(catalogo)


def test_token_raro_identifica_el_producto():
    catalogo = {"col-china-longapa": "Col China/Longapa", "col-corazon": "Col Corazon"}
    assert R.detectar_productos("¿qué pasó con la longapa?", catalogo) == \
        frozenset({"col-china-longapa"})


def test_pregunta_sin_tokens(catalogo):
    assert R.detectar_productos("¿?", catalogo) == frozenset()


# --------------------------------------------------------------------------- #
# Detección de fechas — siempre relativa al último dato, no al reloj
# --------------------------------------------------------------------------- #
REF = "2026-08-07"  # un viernes


@pytest.mark.parametrize("pregunta,desde,hasta", [
    ("¿qué está más barato hoy?", REF, REF),
    ("¿cuánto costaba ayer?", "2026-08-06", "2026-08-06"),
    ("¿por qué subió esta semana?", "2026-08-03", REF),        # lunes..ref
    ("¿y la semana pasada?", "2026-07-27", "2026-08-02"),
    ("¿cómo estuvo este mes?", "2026-08-01", REF),
    ("¿y el mes pasado?", "2026-07-01", "2026-07-31"),
    ("¿qué pasó en julio?", "2026-07-01", "2026-07-31"),
    ("¿qué pasó en diciembre?", "2025-12-01", "2025-12-31"),   # aún no llegó: año previo
    ("¿qué pasó en marzo de 2025?", "2025-03-01", "2025-03-31"),
    ("el 2026-01-15", "2026-01-15", "2026-01-15"),
    # Día concreto en español: más específico que el mes, tiene que ganarle.
    ("¿cómo estuvo el mercado el 15 de julio?", "2026-07-15", "2026-07-15"),
    ("el 3 de marzo de 2025", "2025-03-03", "2025-03-03"),
    ("el 20 de diciembre", "2025-12-20", "2025-12-20"),   # aún no llegó: año previo
    ("en los últimos 30 días", "2026-07-08", REF),
    ("¿cómo fue el año pasado?", "2025-01-01", "2025-12-31"),
])
def test_rango_de_fechas(pregunta, desde, hasta):
    assert R.detectar_rango_fechas(pregunta, REF) == (desde, hasta)


def test_sin_expresion_temporal_no_filtra():
    assert R.detectar_rango_fechas("¿cuánto cuesta la papa?", REF) == (None, None)


def test_fecha_imposible_cae_al_mes():
    """'31 de febrero' no existe: en vez de reventar, se toma el mes entero."""
    assert R.detectar_rango_fechas("el 31 de febrero de 2026", REF) == \
        ("2026-02-01", "2026-02-28")


def test_dia_concreto_gana_al_mes():
    dia = R.detectar_rango_fechas("el 15 de julio de 2026", REF)
    mes = R.detectar_rango_fechas("en julio de 2026", REF)
    assert dia == ("2026-07-15", "2026-07-15")
    assert mes == ("2026-07-01", "2026-07-31")


def test_sin_fecha_de_referencia_no_filtra():
    assert R.detectar_rango_fechas("esta semana", None) == (None, None)


def test_pregunta_estacional_no_se_ancla_a_un_ano():
    """'¿cuánto suele costar en agosto?' pregunta por los agostos en general.
    Anclarla a agosto de este año dejaría fuera la evidencia que la responde."""
    assert R.es_estacional("¿cuánto suele costar la zanahoria en agosto?")
    assert R.detectar_rango_fechas(
        "¿cuánto suele costar la zanahoria en agosto?", REF) == (None, None)
    # pero la misma pregunta SIN marca estacional sí acota
    assert R.detectar_rango_fechas("¿cuánto costó en agosto?", REF) != (None, None)


@pytest.mark.parametrize("pregunta", [
    "¿cuánto suele costar?", "¿cuál es el promedio histórico?",
    "normalmente cuánto vale", "¿es lo típico para esta época?",
])
def test_marcas_estacionales(pregunta):
    assert R.es_estacional(pregunta)


def test_no_es_estacional():
    assert not R.es_estacional("¿cuánto cuesta hoy la papa?")


# --------------------------------------------------------------------------- #
# Detección de tipo de chunk
# --------------------------------------------------------------------------- #
def test_pregunta_por_anomalias():
    tipos = R.detectar_tipos("¿hubo algo raro o anómalo con la cebolla?")
    assert TIPO_EVENTO_ANOMALIA in tipos


def test_pregunta_estacional_apunta_al_perfil():
    tipos = R.detectar_tipos("¿cuánto suele costar en agosto?")
    assert TIPO_PRODUCTO_PERFIL in tipos


def test_pregunta_neutra_no_restringe_tipo():
    """Un filtro de tipo equivocado esconde el chunk correcto: ante la duda, no
    filtrar."""
    assert R.detectar_tipos("¿cuánto cuesta la papa?") is None


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #
def test_bm25_prioriza_el_documento_relevante(snapshot_sintetico):
    chunks = build_corpus(snapshot_sintetico, "semana")
    bm = R.BM25(chunks)
    res = bm.buscar("papaya", k=5)
    assert res
    mejor = chunks[res[0][0]]
    assert "Papaya" in mejor.texto


def test_bm25_respeta_el_conjunto_permitido(snapshot_sintetico):
    chunks = build_corpus(snapshot_sintetico, "semana")
    bm = R.BM25(chunks)
    permitidos = {i for i, c in enumerate(chunks) if c.slug == "tomate"}
    for i, _ in bm.buscar("precio", k=10, permitidos=permitidos):
        assert chunks[i].slug == "tomate"


def test_bm25_sin_coincidencias(snapshot_sintetico):
    chunks = build_corpus(snapshot_sintetico, "semana")
    assert R.BM25(chunks).buscar("xyzzyqwerty", k=5) == []


def test_bm25_es_reproducible(snapshot_sintetico):
    chunks = build_corpus(snapshot_sintetico, "semana")
    bm = R.BM25(chunks)
    a = bm.buscar("precio de la papa", k=10)
    assert a == bm.buscar("precio de la papa", k=10)


# --------------------------------------------------------------------------- #
# RRF
# --------------------------------------------------------------------------- #
def test_rrf_premia_estar_arriba_en_ambas_listas():
    fusion = R.rrf([["a", "b", "c"], ["b", "a", "c"]])
    assert fusion["b"] > fusion["c"]
    assert fusion["a"] > fusion["c"]


def test_rrf_suma_las_listas():
    fusion = R.rrf([["a"], ["a"]])
    assert fusion["a"] == pytest.approx(2 / (R.RRF_C + 1))


def test_rrf_con_listas_disjuntas():
    fusion = R.rrf([["a"], ["b"]])
    assert fusion["a"] == pytest.approx(fusion["b"])


def test_rrf_lista_vacia():
    assert R.rrf([[], []]) == {}


# --------------------------------------------------------------------------- #
# Piso determinista — la garantía de producto
# --------------------------------------------------------------------------- #
def test_producto_nombrado_siempre_trae_contexto(recuperador):
    c = recuperador.recuperar("¿cuánto está el tomate?", k=5)
    assert c.piso, "un producto nombrado debe traer contexto garantizado"
    assert all(x.slug == "tomate" for x in c.piso)


def test_piso_respeta_el_presupuesto(recuperador):
    """Sin tope, 'papa' (varias variedades) inundaría el prompt."""
    for pregunta in ("¿por qué subió la papa?", "¿cuánto está el tomate?",
                     "compara papa con tomate y papaya"):
        c = recuperador.recuperar(pregunta, k=5)
        assert len(c.piso) <= R.PISO_PRESUPUESTO, pregunta


def test_muchos_productos_traen_al_menos_uno_cada_uno(recuperador):
    c = recuperador.recuperar("¿por qué subió la papa?", k=5)
    slugs = {x.slug for x in c.piso}
    assert slugs == {"papa-blanca", "papa-amarilla"}


def test_con_fecha_se_prioriza_la_ventana_pedida(recuperador, snapshot_sintetico):
    """Quien pregunta por una semana quiere ESA semana, no el resumen histórico."""
    fecha = snapshot_sintetico["_fecha_anomalia"]
    c = recuperador.recuperar(f"¿qué pasó con el tomate el {fecha}?", k=5)
    periodos = [x for x in c.piso if x.tipo == TIPO_PRODUCTO_PERIODO]
    assert periodos
    assert any(x.fecha_inicio <= fecha <= x.fecha_fin for x in periodos)


def test_sin_fecha_el_perfil_va_primero(recuperador):
    c = recuperador.recuperar("¿cuánto está el tomate?", k=5)
    assert c.piso[0].tipo == TIPO_PRODUCTO_PERFIL


def test_pregunta_agregada_trae_el_resumen_del_dia(recuperador):
    """'¿qué está más barato hoy?' no vive en ningún producto: vive en el
    resumen del día."""
    c = recuperador.recuperar("¿qué está más barato hoy?", k=5)
    assert c.consulta.slugs == frozenset()
    assert c.piso
    assert all(x.tipo == TIPO_MERCADO_DIA for x in c.piso)


def test_pregunta_agregada_sin_fecha_no_rompe(recuperador):
    c = recuperador.recuperar("¿qué recomiendas comprar?", k=5)
    assert isinstance(c.piso, list)


# --------------------------------------------------------------------------- #
# Recuperación completa
# --------------------------------------------------------------------------- #
def test_devuelve_como_mucho_k(recuperador):
    assert len(recuperador.recuperar("papa", k=3).recuperados) <= 3


def test_filtro_demasiado_restrictivo_se_relaja(recuperador):
    """Un filtro que no deja nada dejaría la pregunta sin contexto. Mejor
    ampliar la búsqueda que devolver vacío."""
    c = recuperador.recuperar("¿cuánto costó el tomate en 1999?", k=5)
    assert c.recuperados or c.piso


def test_chunks_no_repite_entre_piso_y_recuperados(recuperador):
    c = recuperador.recuperar("¿cuánto está el tomate?", k=8)
    ids = [x.id for x in c.chunks()]
    assert len(ids) == len(set(ids))


def test_el_piso_va_primero(recuperador):
    c = recuperador.recuperar("¿cuánto está el tomate?", k=8)
    assert [x.id for x in c.chunks()[:len(c.piso)]] == [x.id for x in c.piso]


def test_prompt_separa_lo_garantizado_de_lo_recuperado(recuperador):
    prompt = recuperador.recuperar("¿cuánto está el tomate?", k=5).a_prompt()
    assert "CONTEXTO GARANTIZADO" in prompt
    assert "CONTEXTO RECUPERADO" in prompt
    assert prompt.index("CONTEXTO GARANTIZADO") < prompt.index("CONTEXTO RECUPERADO")


def test_encabezado_del_piso_describe_lo_que_contiene(recuperador):
    """Decirle al modelo "productos que menciona la pregunta" sobre un resumen de
    mercado sería describirle mal lo que está leyendo."""
    con_producto = recuperador.recuperar("¿cuánto está el tomate?", k=5).a_prompt()
    assert "productos que menciona la pregunta" in con_producto

    agregada = recuperador.recuperar("¿qué está más barato hoy?", k=5).a_prompt()
    assert "resumen del mercado" in agregada
    assert "productos que menciona la pregunta" not in agregada


def test_recuperacion_es_reproducible(recuperador):
    a = recuperador.recuperar("¿por qué subió el tomate?", k=6)
    b = recuperador.recuperar("¿por qué subió el tomate?", k=6)
    assert [x.chunk.id for x in a.recuperados] == [x.chunk.id for x in b.recuperados]
    assert [x.id for x in a.piso] == [x.id for x in b.piso]


def test_la_fecha_de_referencia_es_la_del_dato(recuperador, snapshot_sintetico):
    """No la de hoy: si el pipeline no corrió, 'esta semana' debe ser la última
    semana CON DATOS."""
    assert recuperador.fecha_ref == snapshot_sintetico["latestFecha"]


def test_pregunta_vacia_no_rompe(recuperador):
    c = recuperador.recuperar("", k=5)
    assert isinstance(c.recuperados, list)


# --------------------------------------------------------------------------- #
# El objetivo funcional del bloque
# --------------------------------------------------------------------------- #
def test_por_que_subio_recupera_serie_y_anomalia(recuperador, snapshot_sintetico):
    """El objetivo declarado: "¿por qué subió X?" tiene que traer la serie
    reciente y la anomalía detectada, no una sola fila."""
    c = recuperador.recuperar("¿por qué subió el tomate esta semana?", k=10)
    tipos = {x.tipo for x in c.chunks()}
    assert TIPO_PRODUCTO_PERIODO in tipos, "falta la serie de la ventana"
    texto = c.a_prompt()
    assert "Tomate" in texto
    # la ventana con el salto inyectado tiene que estar en el contexto
    assert any(x.slug == "tomate" and x.tipo == TIPO_PRODUCTO_PERIODO
               for x in c.chunks())


def test_anomalia_llega_al_contexto_al_preguntar_por_ella(recuperador):
    c = recuperador.recuperar("¿hubo algo anómalo con el tomate?", k=10)
    assert any(x.tipo == TIPO_EVENTO_ANOMALIA and x.slug == "tomate"
               for x in c.chunks())
