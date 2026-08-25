"""Recuperación híbrida de Precio Vivo: filtro estructurado + BM25 + vectores.

LAS CUATRO PIEZAS, Y POR QUÉ CADA UNA
-------------------------------------
1. PRE-FILTRO ESTRUCTURADO. La pregunta se parsea buscando productos (73 nombres
   canónicos) y expresiones de fecha en español. Se aplica como WHERE ANTES del
   k-NN. Con un catálogo de 73 nombres el match léxico es CONFIABLE — eso es una
   ventaja del dominio, no una limitación: si alguien dice "papa", sabemos con
   certeza de qué habla y no hay razón para delegarlo a un coseno.

2. BM25. Ranking léxico clásico sobre el texto de los chunks. Atrapa lo que el
   vector pierde: cifras exactas, nombres propios raros, fechas escritas.

3. VECTORES. Atrapa lo que el léxico pierde: sinónimos que no están en el dato
   (palta/aguacate), preguntas por categoría ("¿qué tubérculos subieron?"),
   paráfrasis.

4. FUSIÓN RRF. Reciprocal Rank Fusion combina ambos rankings sin necesidad de
   normalizar scores entre escalas incomparables (BM25 no está acotado, el coseno
   sí). Suma 1/(C+rango) por lista.

EL PISO DETERMINISTA
--------------------
La pieza que hace esto utilizable en un producto real. Si la pregunta nombra un
producto, su ficha, sus últimas semanas y sus anomalías se adjuntan SIEMPRE,
gane lo que gane la recuperación. La búsqueda AMPLÍA el contexto; nunca es la
puerta que decide si los hechos evidentes llegan al modelo.

El motivo es de producto, no de arquitectura: un asistente que a veces falla lo
obvio ("¿a cuánto está la papa?") es peor que no tener RAG, porque destruye la
confianza en todas las respuestas, incluidas las buenas. La recuperación
probabilística está bien para descubrir contexto; no para garantizarlo.

FECHA DE REFERENCIA
-------------------
"esta semana" se resuelve contra `snapshot.latestFecha`, no contra el reloj. Si
el pipeline no corrió hoy, "esta semana" debe significar la última semana CON
DATOS, no una ventana vacía que existe solo en el calendario.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .corpus import (
    GRANULARIDAD_DEFAULT,
    TIPO_EVENTO_ANOMALIA,
    TIPO_MERCADO_DIA,
    TIPO_PRODUCTO_PERFIL,
    TIPO_PRODUCTO_PERIODO,
    Chunk,
    build_corpus,
    cargar_snapshot,
)
from .embeddings import Embedder, get_embedder
from .vectorstore import Filtro, IndiceVectorial, NumpyIndex, Resultado

# Constante de RRF. 60 es el valor del paper original y funciona bien sin tunear;
# amortigua las primeras posiciones para que una lista no domine a la otra.
RRF_C = 60
# Cuántos candidatos pide cada recuperador antes de fusionar.
CANDIDATOS = 40
# Semanas recientes del producto que entran al piso determinista.
PISO_PERIODOS = 4
# Anomalías del producto que entran al piso determinista.
PISO_ANOMALIAS = 3
# Tope TOTAL de chunks del piso. Existe porque "papa" matchea 10 variedades: sin
# tope el piso serían 80 chunks (~50 KB) y ahogaría al contexto recuperado. Con
# tope, muchos productos -> ficha de cada uno; un solo producto -> su historia
# en profundidad. El reparto lo hace `_piso`.
PISO_PRESUPUESTO = 14
# Chunks de mercado-día que entran al piso en preguntas AGREGADAS (sin producto).
PISO_MERCADO_DIA = 3

_MESES_NOMBRE = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# Palabras que nunca deben disparar un match de producto: son demasiado comunes
# y algunos nombres canónicos las contienen ("Maiz Choclo Serrano" -> "serrano").
_PALABRAS_VACIAS = {
    "que", "cual", "cuales", "como", "cuando", "donde", "por", "para", "con",
    "sin", "del", "las", "los", "una", "uno", "esta", "este", "esa", "ese",
    "mas", "menos", "muy", "hoy", "ayer", "semana", "mes", "ano", "dia", "dias",
    "precio", "precios", "costo", "cuesta", "vale", "subio", "bajo", "sube",
    "baja", "mercado", "kilo", "kilos", "soles", "toneladas", "ingreso",
}


def normalizar(s: str) -> str:
    """Minúsculas sin acentos. Base de todo el matching léxico."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return s.lower().strip()


def tokenizar(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", normalizar(s)) if t]


# --------------------------------------------------------------------------- #
# Parseo de la pregunta
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Consulta:
    """Una pregunta ya parseada en sus componentes estructurados."""
    texto: str
    slugs: frozenset[str] = frozenset()
    fecha_desde: str | None = None
    fecha_hasta: str | None = None
    tipos: frozenset[str] | None = None

    def a_filtro(self) -> Filtro:
        return Filtro(
            slugs=self.slugs or None,
            tipos=self.tipos,
            fecha_desde=self.fecha_desde,
            fecha_hasta=self.fecha_hasta,
        )


def _tokens_significativos(nombre: str) -> list[str]:
    """Tokens del nombre canónico que sirven para identificarlo.

    Descarta conectores y ruido de formato: "Ajo Criollo O Napuri" -> [ajo,
    criollo, napuri]; "Caigua (Selva)" -> [caigua, selva].
    """
    return [t for t in tokenizar(nombre)
            if len(t) > 2 and t not in _PALABRAS_VACIAS]


def catalogo_de(snap: dict) -> dict[str, str]:
    """{slug: nombre} de TODO lo que el snapshot publica con precio.

    Los 72 del GMML más los de `mercados` — hoy el pollo vivo que llega por
    SISAP, mañana lo que traiga el boletín.

    POR QUÉ IMPORTA QUE ESTÉN LOS DOS
    ---------------------------------
    Este catálogo es lo que `detectar_productos` usa para decidir si la pregunta
    nombra algo del inventario. Cuando solo traía los del GMML, "¿cómo va el
    pollo vivo?" no identificaba producto, se disparaba el AVISO de "no está en
    el catálogo" y el modelo respondía que no lo seguíamos — con el precio en el
    mismo JSON y pintado en el dashboard.

    Una negación afirmada como hecho es peor que un "no sé", así que la
    corrección no es de prompt: es que el catálogo describa lo que el producto
    realmente cubre.
    """
    catalogo = {p["slug"]: p["nombre"] for p in snap.get("productos") or []}
    for m in snap.get("mercados") or []:
        for prod in m.get("productos") or []:
            if prod.get("slug") and prod.get("nombre"):
                # `setdefault`: si un slug coincidiera entre mercados, manda el
                # del GMML, que es el que tiene serie histórica.
                catalogo.setdefault(prod["slug"], prod["nombre"])
    return catalogo


def detectar_productos(pregunta: str, catalogo: dict[str, str]) -> frozenset[str]:
    """Productos mencionados en la pregunta. `catalogo`: {slug: nombre}.

    Se cuenta cuántos tokens del nombre canónico aparecen en la pregunta, y se
    resuelve en dos niveles:

      ESPECÍFICO (>= 2 tokens): "ajo morado" y "ajo criollo" identifican dos
      productos distintos, aunque el canónico del segundo sea "Ajo Criollo O
      Napuri" y nadie lo escriba entero. Contar tokens en vez de exigir el nombre
      completo es lo que hace que la comparación entre ambos funcione.

      AGRUPADO (1 token): "¿por qué subió la papa?" no nombra una variedad, así
      que se traen las diez.

    La supresión del nivel agrupado es POR FAMILIA, no global. Si alguien dice
    "papa blanca", papa-blanca llega a 2 tokens y las otras papas se quedan en 1,
    así que la familia "papa" NO se expande — pero cualquier OTRA familia que la
    pregunta nombre sigue entrando.

    POR QUÉ POR FAMILIA Y NO GLOBAL
    --------------------------------
    Era global: bastaba con que un producto llegara a 2 tokens para descartar
    TODOS los de uno. Medido, con el catálogo real:

        "compara el tomate con el brocoli"        -> tomate, brocoli      1+1 ok
        "compara el ajo morado con la papa blanca"-> los dos              2+2 ok
        "compara el tomate con la papa blanca"    -> SOLO papa-blanca     1+2 mal
        "compara el ajo morado con el tomate"     -> SOLO ajo-morado      2+1 mal
        "compara el limon sutil bolsa con tomate" -> SOLO los limones     3+1 mal

    Es decir: cualquier comparación entre dos productos de nombres con distinto
    número de palabras perdía el corto, en silencio. El piso llenaba entonces sus
    ocho plazas con el producto superviviente y el modelo respondía la comparación
    con la mitad de la evidencia — verificado hasta k=30.

    No lo veía nadie porque los dos casos de comparación del gold set emparejan
    nombres de igual longitud ("ajo morado"/"ajo criollo", "camote amarillo"/
    "camote morado").

    Todo el matching es por token completo, nunca por subcadena: si no, "papa"
    matchearía dentro de "papaya" y la respuesta mezclaría dos productos.
    """
    en_pregunta = set(tokenizar(pregunta))
    if not en_pregunta:
        return frozenset()

    aciertos: dict[str, int] = {}
    cabezas: dict[str, list[str]] = {}
    for slug, nombre in catalogo.items():
        tokens = _tokens_significativos(nombre)
        if not tokens:
            continue
        aciertos[slug] = sum(1 for t in tokens if t in en_pregunta)
        cabezas.setdefault(tokens[0], []).append(slug)

    especificos = {s for s, n in aciertos.items() if n >= 2}

    # Familias que ya quedaron resueltas por un match específico. Dentro de
    # ellas no se expande: quien dijo "papa blanca" no pidió las diez papas.
    familias_resueltas = {cabeza for cabeza, slugs in cabezas.items()
                          if any(s in especificos for s in slugs)}

    # Agrupado por la primera palabra del nombre, que es la que nombra la
    # familia — salvo en las familias ya resueltas.
    agrupados: set[str] = set()
    for cabeza, slugs in cabezas.items():
        if cabeza in en_pregunta and cabeza not in familias_resueltas:
            agrupados.update(slugs)

    if especificos or agrupados:
        return frozenset(especificos | agrupados)

    # Último recurso: un solo token coincidente que no es cabeza de familia
    # (p. ej. "napuri", "longapa", "hidroponica").
    #
    # SIGUE SIENDO ÚLTIMO RECURSO A PROPÓSITO, y no se une a los anteriores como
    # sí se hace con el agrupado. `_tokens_significativos` solo descarta
    # conectores y palabras de dos letras, así que aquí sobreviven tokens de
    # unidad —"bolsa", "cajon", "docena", "atado"— que aparecen en nombres de
    # producto Y en preguntas corrientes. Unirlo convertiría "¿la papa blanca
    # viene en bolsa?" en una consulta sobre el limón. El nivel específico y el
    # agrupado se anclan en el nombre del producto; éste no, y por eso solo
    # actúa cuando no hay nada mejor.
    return frozenset(s for s, n in aciertos.items() if n == 1)


def es_estacional(pregunta: str) -> bool:
    """True si la pregunta va sobre lo TÍPICO, no sobre un periodo concreto.

    "¿cuánto suele costar la zanahoria en agosto?" nombra un mes, pero no
    pregunta por agosto de este año: pregunta por los agostos en general. Anclar
    el filtro a un solo año dejaría fuera justo la evidencia que la responde.
    """
    return bool(re.search(
        r"suele|solia|normalmente|habitual|historic|estacional|tipic|"
        r"en promedio|promedio historic|todos los anos|cada ano",
        normalizar(pregunta)))


def detectar_rango_fechas(
    pregunta: str, fecha_ref: str | None,
) -> tuple[str | None, str | None]:
    """Traduce expresiones temporales en español a un rango ISO [desde, hasta].

    `fecha_ref` es la fecha del último dato, no la de hoy: si el pipeline no
    corrió, "esta semana" tiene que ser la última semana CON DATOS.
    """
    if not fecha_ref:
        return None, None
    try:
        ref = date.fromisoformat(fecha_ref)
    except (TypeError, ValueError):
        return None, None
    t = normalizar(pregunta)

    # Fecha explícita ISO.
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return m.group(1), m.group(1)

    # Pregunta estacional: el mes que nombra NO acota el rango. La ficha del
    # producto trae el promedio histórico mes a mes, que es lo que la responde.
    if es_estacional(pregunta):
        return None, None

    # "el 15 de julio [de 2026]" -> UN día concreto. Va antes que el patrón de
    # mes porque es más específico: sin esto, "el 15 de julio" se ensancharía a
    # todo julio y el día pedido se perdería entre los otros veinte.
    m = re.search(rf"\b(\d{{1,2}})\s+de\s+({'|'.join(_MESES_NOMBRE)})\b"
                  rf"(?:\s+de[l]?\s+(\d{{4}}))?", t)
    if m:
        dia, mes = int(m.group(1)), _MESES_NOMBRE[m.group(2)]
        anio = int(m.group(3)) if m.group(3) else ref.year
        if not m.group(3) and (mes, dia) > (ref.month, ref.day):
            anio -= 1
        try:
            d = date(anio, mes, dia)
            return d.isoformat(), d.isoformat()
        except ValueError:
            pass  # "31 de febrero": se cae al patrón de mes

    # "en julio [de 2025]" / "durante agosto"
    m = re.search(rf"\b({'|'.join(_MESES_NOMBRE)})\b(?:\s+de\s+(\d{{4}}))?", t)
    if m:
        mes = _MESES_NOMBRE[m.group(1)]
        anio = int(m.group(2)) if m.group(2) else ref.year
        # Sin año explícito y el mes aún no ocurrió este año -> fue el anterior.
        if not m.group(2) and mes > ref.month:
            anio -= 1
        ini = date(anio, mes, 1)
        fin = (date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)) - timedelta(days=1)
        return ini.isoformat(), fin.isoformat()

    if re.search(r"\bhoy\b|\bultimo dia\b|\bactual(mente)?\b", t):
        return ref.isoformat(), ref.isoformat()
    if re.search(r"\bayer\b", t):
        d = ref - timedelta(days=1)
        return d.isoformat(), d.isoformat()

    m = re.search(r"ultim[oa]s?\s+(\d{1,3})\s+dias?", t)
    if m:
        return (ref - timedelta(days=int(m.group(1)))).isoformat(), ref.isoformat()

    if re.search(r"semana pasada|semana anterior", t):
        lunes = ref - timedelta(days=ref.weekday())
        return (lunes - timedelta(days=7)).isoformat(), (lunes - timedelta(days=1)).isoformat()
    if re.search(r"esta semana|ultima semana|semana", t):
        return (ref - timedelta(days=ref.weekday())).isoformat(), ref.isoformat()

    if re.search(r"mes pasado|mes anterior", t):
        primero = ref.replace(day=1)
        fin = primero - timedelta(days=1)
        return fin.replace(day=1).isoformat(), fin.isoformat()
    if re.search(r"este mes|ultimo mes", t):
        return ref.replace(day=1).isoformat(), ref.isoformat()

    if re.search(r"ano pasado|year pasado|anio pasado", t):
        return date(ref.year - 1, 1, 1).isoformat(), date(ref.year - 1, 12, 31).isoformat()
    if re.search(r"este ano|este anio", t):
        return date(ref.year, 1, 1).isoformat(), ref.isoformat()

    return None, None


def detectar_tipos(pregunta: str) -> frozenset[str] | None:
    """Restringe el tipo de chunk cuando la pregunta lo pide sin ambigüedad.

    Conservador a propósito: solo cuando la señal es inequívoca. Un filtro de
    tipo equivocado esconde el chunk correcto, y eso es peor que no filtrar.
    """
    t = normalizar(pregunta)
    if re.search(r"anomal|raro|extrano|inusual|salto|atipic", t):
        return frozenset({TIPO_EVENTO_ANOMALIA, TIPO_PRODUCTO_PERIODO})
    if re.search(r"suele|normalmente|historic|estacional|tipico|promedio anual", t):
        return frozenset({TIPO_PRODUCTO_PERFIL, TIPO_PRODUCTO_PERIODO})
    return None


def parsear(pregunta: str, catalogo: dict[str, str], fecha_ref: str | None) -> Consulta:
    desde, hasta = detectar_rango_fechas(pregunta, fecha_ref)
    return Consulta(
        texto=pregunta,
        slugs=detectar_productos(pregunta, catalogo),
        fecha_desde=desde, fecha_hasta=hasta,
        tipos=detectar_tipos(pregunta),
    )


# --------------------------------------------------------------------------- #
# BM25 (léxico)
# --------------------------------------------------------------------------- #
class BM25:
    """BM25 Okapi sobre el texto de los chunks. Índice invertido en memoria.

    A ~9.100 chunks el índice se arma en menos de un segundo y cada consulta
    recorre solo los posting lists de sus términos.
    """

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._postings: dict[str, list[tuple[int, int]]] = {}
        self._largos: list[int] = []
        for i, c in enumerate(chunks):
            tokens = tokenizar(c.texto)
            self._largos.append(len(tokens))
            frec: dict[str, int] = {}
            for tok in tokens:
                frec[tok] = frec.get(tok, 0) + 1
            for tok, f in frec.items():
                self._postings.setdefault(tok, []).append((i, f))
        n = len(chunks)
        self._largo_medio = (sum(self._largos) / n) if n else 0.0
        self._idf = {
            tok: math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for tok, post in self._postings.items()
        }

    def buscar(self, pregunta: str, k: int, permitidos: set[int] | None = None
               ) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for tok in set(tokenizar(pregunta)):
            post = self._postings.get(tok)
            if not post:
                continue
            idf = self._idf[tok]
            for i, f in post:
                if permitidos is not None and i not in permitidos:
                    continue
                norm = 1 - self.b + self.b * (self._largos[i] / self._largo_medio
                                              if self._largo_medio else 1.0)
                scores[i] = scores.get(i, 0.0) + idf * (f * (self.k1 + 1)) / (
                    f + self.k1 * norm)
        # Desempate por id de chunk, igual que el vectorial: orden reproducible.
        orden = sorted(scores.items(), key=lambda kv: (-kv[1], self.chunks[kv[0]].id))
        return orden[:k]


# --------------------------------------------------------------------------- #
# Fusión
# --------------------------------------------------------------------------- #
def rrf(listas: list[list[str]], c: int = RRF_C) -> dict[str, float]:
    """Reciprocal Rank Fusion: {id_chunk: score fusionado}.

    Combina rankings sin normalizar scores entre sí. BM25 no está acotado y el
    coseno vive en [-1,1]; mezclarlos por valor exigiría calibrarlos. RRF solo
    mira POSICIONES, así que no hace falta.
    """
    out: dict[str, float] = {}
    for lista in listas:
        for rango, cid in enumerate(lista, start=1):
            out[cid] = out.get(cid, 0.0) + 1.0 / (c + rango)
    return out


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #
@dataclass
class Contexto:
    """Lo que la recuperación entrega al modelo.

    `piso` y `recuperados` van SEPARADOS a propósito: el piso son los hechos que
    garantizamos, los recuperados son los que la búsqueda propuso. El prompt los
    presenta distinto y quien lea el código ve cuál es cuál.
    """
    pregunta: str
    consulta: Consulta
    piso: list[Chunk] = field(default_factory=list)
    recuperados: list[Resultado] = field(default_factory=list)
    n_candidatos: int = 0

    def chunks(self) -> list[Chunk]:
        """Piso primero, luego recuperados, sin repetir."""
        vistos = {c.id for c in self.piso}
        return self.piso + [r.chunk for r in self.recuperados if r.chunk.id not in vistos]

    def a_prompt(self) -> str:
        """Serializa el contexto para el LLM.

        El encabezado del piso describe QUÉ es: si la pregunta nombró productos,
        son los hechos de esos productos; si no, es el resumen del mercado. Decir
        "productos que menciona la pregunta" sobre un resumen de mercado sería
        describirle mal al modelo lo que está leyendo.
        """
        partes: list[str] = []
        if not self.consulta.slugs:
            # AVISO EXPLÍCITO DE QUE NO SE IDENTIFICÓ PRODUCTO.
            #
            # El filtro léxico ya lo sabe —con 72 nombres canónicos el match es
            # confiable— pero esa información se perdía antes de llegar al
            # modelo, y la búsqueda vectorial rellena el hueco con lo más
            # parecido que encuentre. Medido con un embebedor real:
            # "¿cuánto cuesta la papaya?" (que NO está en el catálogo) devuelve
            # fichas de papa a coseno 0.644, contra 0.666 de una pregunta por
            # papa de verdad. Dos centésimas: ningún umbral las separa, y uno
            # que lo intentara mataría las preguntas agregadas (0.515).
            #
            # Así que no se arregla filtrando, se arregla DICIÉNDOLO. El modelo
            # recibe fichas de papa junto al aviso de que ninguna corresponde a
            # lo que se preguntó, y puede responder "no tenemos ese producto"
            # en vez de dar el precio de otro.
            partes.append(
                "=== AVISO ===\n"
                "Ninguna palabra de la pregunta coincide con un producto del "
                "catálogo. Si preguntan por un producto concreto, NO está entre "
                "los que seguimos: dilo explícitamente y no respondas con el "
                "precio de otro producto por parecido. El contexto de abajo es "
                "lo más cercano que encontró la búsqueda, no una coincidencia.")
        if self.piso:
            que = ("productos que menciona la pregunta" if self.consulta.slugs
                   else "resumen del mercado en las fechas relevantes")
            partes.append(f"=== CONTEXTO GARANTIZADO ({que}) ===")
            partes.extend(c.texto for c in self.piso)
        recuperados = [r for r in self.recuperados
                       if r.chunk.id not in {c.id for c in self.piso}]
        if recuperados:
            partes.append("=== CONTEXTO RECUPERADO (por relevancia) ===")
            partes.extend(r.chunk.texto for r in recuperados)
        return "\n\n".join(partes)


# --------------------------------------------------------------------------- #
# Recuperador
# --------------------------------------------------------------------------- #
class Recuperador:
    """Une catálogo, corpus, BM25 e índice vectorial detrás de `recuperar`."""

    def __init__(self, chunks: list[Chunk], indice: IndiceVectorial,
                 embedder: Embedder, catalogo: dict[str, str],
                 fecha_ref: str | None):
        self.chunks = chunks
        self.indice = indice
        self.embedder = embedder
        self.catalogo = catalogo
        self.fecha_ref = fecha_ref
        self.bm25 = BM25(chunks)
        self._por_id = {c.id: c for c in chunks}
        self._indice_de = {c.id: i for i, c in enumerate(chunks)}

    @classmethod
    def desde_snapshot(
        cls, origen: Any, embedder: Embedder | None = None,
        granularidad: str = GRANULARIDAD_DEFAULT,
        indice: IndiceVectorial | None = None,
    ) -> "Recuperador":
        """Construye todo desde un snapshot, embebiendo el corpus en el momento.

        Cómodo para tests y para la CLI. En producción el índice se construye una
        vez con `ingest --index` y se carga ya hecho.
        """
        from .indexer import embed_con_cache

        snap = cargar_snapshot(origen)
        chunks = build_corpus(snap, granularidad=granularidad)
        emb = embedder or get_embedder()
        idx = indice or NumpyIndex()
        # Vía la caché de embeddings: sin esto, cada corrida de las evaluaciones
        # con un embebedor REAL re-embebía los ~9.200 chunks —1,5 M de tokens y
        # veinte minutos— para reconstruir algo idéntico a lo que el backfill ya
        # calculó. Medir la calidad semántica dejaba de ser gratis, y lo que no
        # es gratis se mide poco. Con `FakeEmbedder` la caché no aplica (firma
        # distinta) y todo sigue igual de instantáneo.
        idx.construir(chunks, embed_con_cache(chunks, emb),
                      granularidad, emb.firma)
        catalogo = catalogo_de(snap)
        return cls(chunks, idx, emb, catalogo, snap.get("latestFecha"))

    @classmethod
    def desde_artefacto(
        cls, snapshot: Any, destino: str | None = None,
        embedder: Embedder | None = None,
    ) -> "Recuperador":
        """Carga el índice YA PUBLICADO en vez de reconstruirlo.

        Es el camino de la CLI y del que use el artefacto: reconstruir implicaría
        re-embeber ~9.100 chunks en cada invocación, que con un embebedor de API
        cuesta dinero y segundos por cada pregunta.

        Verifica que el embebedor coincida con el que construyó el índice: si no,
        el coseno compararía espacios distintos y devolvería ruido con total
        confianza.
        """
        from .indexer import DESTINO_WEB, cargar_estatico
        from .vectorstore import IndiceMeta

        snap = cargar_snapshot(snapshot)
        chunks, vectores, metas = cargar_estatico(destino or DESTINO_WEB)
        if not chunks:
            raise RuntimeError(
                "No hay índice publicado. Constrúyelo con: "
                "python -m preciovivo.ingest --index")

        emb = embedder or get_embedder()
        firmas = {m.get("firma_embedder") for m in metas.values()}
        if firmas and emb.firma not in firmas:
            raise RuntimeError(
                f"El índice publicado se construyó con {sorted(firmas)} y estás "
                f"consultando con '{emb.firma}'. Compararían espacios vectoriales "
                f"distintos. Reconstruye el índice o usa el mismo embebedor.")

        idx = NumpyIndex()
        alguna = next(iter(metas.values()), {})
        idx.chunks = chunks
        idx.vectores = vectores
        idx.meta = IndiceMeta(
            firma_embedder=emb.firma, dims=int(vectores.shape[1]) if len(chunks) else 0,
            granularidad=alguna.get("granularidad", GRANULARIDAD_DEFAULT),
            n_chunks=len(chunks), huella_corpus=alguna.get("huella_corpus", ""),
            construido_en=alguna.get("construido_en", ""))
        catalogo = catalogo_de(snap)
        return cls(chunks, idx, emb, catalogo, snap.get("latestFecha"))

    # --- piso determinista ------------------------------------------------
    def _piso(self, consulta: Consulta) -> list[Chunk]:
        """Los hechos que se garantizan, pase lo que pase con la recuperación.

        Con producto mencionado: se reparte PISO_PRESUPUESTO entre los productos.
        Muchas variedades ("papa" son 10) -> la ficha de cada una, y que la
        búsqueda aporte el detalle. Un solo producto -> su historia a fondo.

        Si la pregunta acota fechas, se priorizan las ventanas que SOLAPAN ese
        rango sobre la ficha: quien pregunta "¿por qué subió esta semana?" quiere
        esa semana, no el resumen histórico.

        Sin producto pero con fecha ("¿qué está más barato hoy?"), el piso son
        los chunks de mercado-día: esas preguntas son AGREGADAS y su respuesta no
        vive en ningún producto sino en el resumen del día.
        """
        if not consulta.slugs:
            return self._piso_mercado(consulta)

        n = len(consulta.slugs)
        cupo = max(1, PISO_PRESUPUESTO // n)
        acota_fechas = bool(consulta.fecha_desde or consulta.fecha_hasta)
        rango = Filtro(fecha_desde=consulta.fecha_desde,
                       fecha_hasta=consulta.fecha_hasta)

        out: list[Chunk] = []
        for slug in sorted(consulta.slugs):
            del_prod = [c for c in self.chunks if c.slug == slug]
            perfil = [c for c in del_prod if c.tipo == TIPO_PRODUCTO_PERFIL]
            periodos = sorted((c for c in del_prod if c.tipo == TIPO_PRODUCTO_PERIODO),
                              key=lambda c: c.fecha_fin, reverse=True)
            anomalias = sorted((c for c in del_prod if c.tipo == TIPO_EVENTO_ANOMALIA),
                               key=lambda c: c.fecha_fin, reverse=True)[:PISO_ANOMALIAS]

            if acota_fechas:
                en_rango = [c for c in periodos if rango.acepta(c)]
                fuera = [c for c in periodos if not rango.acepta(c)]
                preferencia = en_rango + perfil + fuera[:PISO_PERIODOS] + anomalias
            else:
                preferencia = perfil + periodos[:PISO_PERIODOS] + anomalias
            out.extend(preferencia[:cupo])
        return out

    def _piso_mercado(self, consulta: Consulta) -> list[Chunk]:
        """Resúmenes de mercado-día para preguntas agregadas."""
        dias = [c for c in self.chunks if c.tipo == TIPO_MERCADO_DIA]
        if consulta.fecha_desde or consulta.fecha_hasta:
            rango = Filtro(fecha_desde=consulta.fecha_desde,
                           fecha_hasta=consulta.fecha_hasta)
            dias = [c for c in dias if rango.acepta(c)]
        return sorted(dias, key=lambda c: c.fecha_fin, reverse=True)[:PISO_MERCADO_DIA]

    # --- recuperación -----------------------------------------------------
    def recuperar(self, pregunta: str, k: int = 8,
                  candidatos: int = CANDIDATOS) -> Contexto:
        consulta = parsear(pregunta, self.catalogo, self.fecha_ref)
        filtro = consulta.a_filtro()

        permitidos_idx: set[int] | None = None
        if not filtro.vacio():
            permitidos_idx = {i for i, c in enumerate(self.chunks) if filtro.acepta(c)}
            # Un filtro que no deja nada es peor que no filtrar: la pregunta
            # quedaría sin contexto. Se relaja a búsqueda abierta y el piso
            # determinista sigue garantizando los hechos del producto.
            if not permitidos_idx:
                filtro = Filtro()
                permitidos_idx = None
                consulta = Consulta(texto=pregunta, slugs=consulta.slugs)

        lex = [self.chunks[i].id
               for i, _ in self.bm25.buscar(pregunta, candidatos, permitidos_idx)]

        vec_res = self.indice.buscar(
            self.embedder.embed_consulta(pregunta), k=candidatos,
            filtro=None if filtro.vacio() else filtro)
        vec = [r.chunk.id for r in vec_res]

        fusion = rrf([lex, vec])
        # Desempate por id, por la misma razón que en el vectorstore.
        orden = sorted(fusion.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        recuperados = [Resultado(chunk=self._por_id[cid], score=score)
                       for cid, score in orden if cid in self._por_id]

        return Contexto(
            pregunta=pregunta, consulta=consulta,
            piso=self._piso(consulta),
            recuperados=recuperados,
            n_candidatos=len(fusion),
        )
