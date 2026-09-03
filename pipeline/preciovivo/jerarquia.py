"""Recuperación jerárquica: se BUSCA en el padre, se ENTREGA el hijo.

DE DÓNDE SALE ESTA DECISIÓN
---------------------------
De la tabla que imprime `run_retrieval.py --granularidad todas`, medida sobre el
gold set de 62 casos el 2026-09-03:

    grano    chunks   recall@k    MRR   span(d)
    dia       39440      0.888  0.724         1
    semana     9428      0.983  0.801         4
    mes        3165      0.983  0.894        29

El grano grueso RECUPERA mejor —menos chunks, cada uno con más señal, menos
vecinos casi idénticos con los que confundirse— y el grano fino entrega evidencia
más AJUSTADA. Son dos cosas distintas y el proyecto ya las medía por separado:
`recall`/`MRR` dicen si el hecho se encuentra, `span` dice si sirve para
responder.

La jerarquía se queda con las dos mitades buenas: busca sobre el mes (MRR 0,894)
y entrega la semana o el día que de verdad cubre la fecha preguntada (span 4 o
1). Es el esquema padre/hijo clásico INVERTIDO: lo normal es embeber hijos
pequeños y devolver el padre grande para dar contexto; aquí el contexto no falta
—el texto es generado y cada chunk ya se explica solo— lo que falta es
PRECISIÓN TEMPORAL, así que se devuelve hacia abajo.

POR QUÉ EN CUOTA ES LA ÚNICA VÍA ASEQUIBLE AL SPAN DE UN DÍA
--------------------------------------------------------
Los hijos NO necesitan vector: se localizan por búsqueda estructurada (slug +
solapamiento de fechas), que no cuesta ni una llamada de embedding. Así que
`semana -> dia` entrega evidencia de un día pagando solo el índice SEMANAL, el
que ya existe.

Conseguir el mismo span en plano exige indexar a grano día, y eso cuesta el
doble. Medido el 2026-09-03 sobre el snapshot, a ~4 caracteres por token:

    grano    chunks   tokens de reindexado
    dia      39 440         ~2 558 904
    semana    9 428         ~1 539 543     <- el de hoy
    mes       3 165           ~654 970

Para calibrar: el saldo de la clave de embeddings era de 4 419 781 tokens, o sea
que un reindexado a grano día se come el 58 % de una sentada y el semanal el
35 %. Es la razón concreta detrás de la frase «reindexar cuesta cuota» del
README, y el argumento de coste a favor de esta pieza: misma cuota que hoy, y
además el grano día en plano recupera PEOR (rec. búsq. 0,587 contra 0,892).

Aun así va apagada por defecto: cuesta 11 puntos de MRR de búsqueda. El
intercambio está medido en `evals/README.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .corpus import TIPO_PRODUCTO_PERIODO, Chunk, build_corpus


def _solape(c: Chunk, d0: str, d1: str) -> int:
    """Días de solapamiento entre el chunk y la ventana. 0 si no se tocan."""
    ini = max(c.fecha_inicio, d0)
    fin = min(c.fecha_fin, d1)
    if ini > fin:
        return 0
    return (date.fromisoformat(fin) - date.fromisoformat(ini)).days + 1


# Cuántos hijos puede aportar un padre. Un mes tiene ~4 semanas o ~22 días: sin
# tope, expandir tres padres llenaría el contexto de un solo producto.
MAX_HIJOS = 6


@dataclass
class Jerarquia:
    """Dos granularidades del mismo corpus, enlazadas por slug y fecha.

    No se guarda un puntero padre->hijo: el enlace se calcula por solapamiento
    de rangos, que es robusto a que los ids cambien de formato. Es la misma
    decisión que tomó el gold set al declarar predicados en vez de ids.
    """
    padres: list[Chunk]
    hijos: list[Chunk]
    _por_slug: dict[str, list[Chunk]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for h in self.hijos:
            if h.tipo == TIPO_PRODUCTO_PERIODO and h.slug:
                self._por_slug.setdefault(h.slug, []).append(h)
        for lista in self._por_slug.values():
            lista.sort(key=lambda c: c.fecha_inicio)

    @classmethod
    def desde_snapshot(cls, snap: dict, grano_padre: str = "mes",
                       grano_hijo: str = "semana") -> "Jerarquia":
        return cls(padres=build_corpus(snap, granularidad=grano_padre),
                   hijos=build_corpus(snap, granularidad=grano_hijo))

    def hijos_de(self, padre: Chunk, desde: str | None, hasta: str | None,
                 tope: int = MAX_HIJOS) -> list[Chunk]:
        """Hijos del padre que solapan la ventana preguntada.

        Sin ventana no se expande: devolver los cuatro hijos de un mes cuando
        nadie preguntó por una fecha concreta cambia un chunk por cuatro sin
        ganar precisión, y el `span` que mejoraría no lo mide nadie porque el
        predicado no lleva fecha.
        """
        if padre.tipo != TIPO_PRODUCTO_PERIODO or not padre.slug:
            return []
        if not (desde or hasta):
            return []
        d0, d1 = desde or hasta, hasta or desde
        dentro = [h for h in self._por_slug.get(padre.slug, [])
                  # el hijo tiene que estar DENTRO del padre y tocar la ventana
                  if h.fecha_inicio >= padre.fecha_inicio
                  and h.fecha_fin <= padre.fecha_fin
                  and h.fecha_inicio <= d1 and d0 <= h.fecha_fin]
        dentro.sort(key=lambda h: (-_solape(h, d0, d1), h.fecha_inicio))

        # LA EXPANSION NO PUEDE PERDER COBERTURA.
        #
        # Recortar a `tope` hijos parecia inofensivo y no lo era. Abril de 2026
        # tiene cinco semanas; la pregunta «¿que paso con el aji montaña a fines
        # de abril?» abre una ventana de mes entero, con lo que las cinco solapan
        # por igual y el desempate cronologico se quedaba con W15, W16 y W17
        # —hasta el 24 de abril—. El predicado pedia el 29. El padre mensual SI
        # lo cubria: expandir cambiaba un chunk correcto por tres incorrectos.
        #
        # La regla es la misma que en `deduplicar`: solo se sustituye al padre si
        # lo que entra cubre el mismo terreno. Si no cabe, se queda el padre —
        # evidencia mas ancha, pero completa. Con ventanas estrechas (un dia, una
        # semana) hacen falta pocos hijos y la expansion si ocurre, que es
        # justo cuando el `span` mas se gana.
        recorte = dentro[:tope]
        ventana_ini = max(padre.fecha_inicio, d0)
        ventana_fin = min(padre.fecha_fin, d1)
        if not _cubren_rango(recorte, ventana_ini, ventana_fin):
            return []
        return recorte

    def expandir(self, recuperados: list[Chunk], desde: str | None,
                 hasta: str | None, tope: int = MAX_HIJOS) -> list[Chunk]:
        """Sustituye cada padre por sus hijos relevantes, conservando el orden.

        Un padre sin hijos que solapen se queda tal cual: es preferible entregar
        evidencia ancha a no entregar ninguna.
        """
        out: list[Chunk] = []
        for c in recuperados:
            hijos = self.hijos_de(c, desde, hasta, tope)
            out.extend(hijos if hijos else [c])
        return out


# --------------------------------------------------------------------------- #
# Deduplicación
# --------------------------------------------------------------------------- #
def deduplicar(chunks: list[Chunk]) -> list[Chunk]:
    """Quita repeticiones y solapamientos padre/hijo, conservando el orden.

    TRES FORMAS DE REPETIR, y las tres aparecen en cuanto hay jerarquía:

      1. EL MISMO CHUNK DOS VECES. El piso determinista y la búsqueda proponen
         los mismos hechos a menudo; `Contexto.chunks()` ya filtraba esto por id,
         pero la expansión introduce chunks que no estaban en ninguna de las dos
         listas y vuelven a colisionar.

      2. PADRE Y HIJO A LA VEZ. Si el piso mete el mes de la papa y la búsqueda
         expandida mete la semana de la papa, el modelo lee dos veces los mismos
         días — una vez promediados y otra no. Peor que redundante: parecen dos
         observaciones independientes que se confirman. Gana el HIJO, que es la
         evidencia más ajustada; ésa es toda la razón de ser de la jerarquía.

      3. TEXTO IDÉNTICO CON ID DISTINTO. Un producto con un solo día de dato
         genera el mismo texto en semana y en mes. El id difiere, así que el
         filtro por id no lo ve.
    """
    vistos_id: set[str] = set()
    vistos_texto: set[str] = set()
    fuera: list[Chunk] = []

    for c in chunks:
        if c.id in vistos_id or c.texto in vistos_texto:
            continue
        vistos_id.add(c.id)
        vistos_texto.add(c.texto)
        fuera.append(c)

    # Segunda pasada para el solapamiento padre/hijo: hace falta conocer TODO lo
    # que se va a entregar antes de poder decidir quién contiene a quién.
    periodos = [c for c in fuera
                if c.tipo == TIPO_PRODUCTO_PERIODO and c.slug]
    contenidos: set[str] = set()
    for padre in periodos:
        hijos = [h for h in periodos
                 if h.id != padre.id and h.slug == padre.slug
                 and h.fecha_inicio >= padre.fecha_inicio
                 and h.fecha_fin <= padre.fecha_fin
                 and (h.fecha_inicio > padre.fecha_inicio
                      or h.fecha_fin < padre.fecha_fin)]
        # EL PADRE SOLO SE VA SI LOS HIJOS LO CUBREN ENTERO.
        #
        # La version anterior lo tiraba en cuanto aparecia UN hijo. Pero un mes
        # tiene ~4 semanas: quedarse con una tira 23 dias de evidencia que el
        # padre si tenia. Medido, costaba 13 puntos de recall de busqueda
        # (0,923 -> 0,788) y 18 de MRR — deduplicar estaba PERDIENDO datos, que
        # es lo unico que un deduplicador no puede hacer.
        #
        # Cubrir "entero" se comprueba uniendo los rangos de los hijos: si la
        # union deja un hueco dentro del padre, el padre se queda.
        if hijos and _cubren(hijos, padre):
            contenidos.add(padre.id)

    return [c for c in fuera if c.id not in contenidos]


def _cubren(hijos: list[Chunk], padre: Chunk) -> bool:
    """¿La unión de los hijos cubre todo el rango del padre, sin huecos?"""
    return _cubren_rango(hijos, padre.fecha_inicio, padre.fecha_fin)


def _cubren_rango(hijos: list[Chunk], ini_obj: str, fin_obj: str) -> bool:
    """¿La unión de los hijos cubre [ini_obj, fin_obj] sin dejar huecos?

    Los datos tienen huecos legítimos —no se publica todos los días— así que
    «cubrir» se mide contra los tramos que EXISTEN: dos chunks consecutivos del
    corpus pueden dejar un fin de semana en medio sin que falte nada. Por eso el
    hueco se tolera hasta el salto natural entre periodos y lo que se exige es
    que el primer hijo empiece a tiempo y el último termine a tiempo.
    """
    if not hijos:
        return False
    tramos = sorted((h.fecha_inicio, h.fecha_fin) for h in hijos)
    if tramos[0][0] > ini_obj:
        return False
    alcance = tramos[0][1]
    for ini, fin in tramos[1:]:
        if ini > _siguiente_dia(alcance) and _hay_salto_real(alcance, ini):
            return False
        if fin > alcance:
            alcance = fin
    return alcance >= fin_obj


def _hay_salto_real(fin_previo: str, ini_siguiente: str) -> bool:
    """Un hueco mayor que un fin de semana largo es un hueco de verdad."""
    d = (date.fromisoformat(ini_siguiente) - date.fromisoformat(fin_previo)).days
    return d > 4


def _siguiente_dia(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()
