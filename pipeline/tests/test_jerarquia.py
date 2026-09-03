"""Pruebas de la recuperación jerárquica y del deduplicador.

TODAS SON REGRESIONES. Las tres formas de perder datos que aparecieron al montar
la jerarquía, cada una medida antes de arreglarse:

  - expandir un padre en hijos recortados tiraba la semana que cubría la fecha
    preguntada (13 puntos de recall de búsqueda);
  - deduplicar tiraba el padre mensual en cuanto veía UNA semana suya, perdiendo
    los otros 23 días;
  - las semanas no anidan en los meses, así que «mes -> semana» no es una
    jerarquía y la expansión no puede cubrir el rango entero.

Un deduplicador que pierde datos es peor que no tenerlo, porque el hueco no se
ve: el contexto sigue pareciendo completo.
"""
from __future__ import annotations

from preciovivo.corpus import TIPO_PRODUCTO_PERFIL, TIPO_PRODUCTO_PERIODO, Chunk
from preciovivo.jerarquia import Jerarquia, deduplicar


def periodo(cid: str, slug: str, d0: str, d1: str, texto: str | None = None) -> Chunk:
    return Chunk(id=cid, tipo=TIPO_PRODUCTO_PERIODO, texto=texto or cid,
                 slug=slug, producto=slug, mercado="GMML",
                 fecha_inicio=d0, fecha_fin=d1)


# Abril de 2026: el mes del fallo real (`aji-montana-alza-abril-2026`).
MES = periodo("p:aji:2026-04", "aji", "2026-04-01", "2026-04-30")
W15 = periodo("p:aji:2026-W15", "aji", "2026-04-06", "2026-04-10")
W16 = periodo("p:aji:2026-W16", "aji", "2026-04-13", "2026-04-17")
W17 = periodo("p:aji:2026-W17", "aji", "2026-04-20", "2026-04-24")
W18 = periodo("p:aji:2026-W18", "aji", "2026-04-27", "2026-04-30")


def _jer(hijos):
    return Jerarquia(padres=[MES], hijos=list(hijos))


# --------------------------------------------------------------------------- #
# Expansión
# --------------------------------------------------------------------------- #
def test_no_expande_si_los_hijos_no_cubren_la_ventana():
    """El fallo exacto de `aji-montana-alza-abril-2026`.

    El predicado pedía un chunk que cubriera el 2026-04-29. El padre mensual lo
    cubría; expandir a las tres primeras semanas (hasta el 24) lo perdía. Ante la
    duda se conserva el padre: evidencia más ancha, pero completa.
    """
    jer = _jer([W15, W16, W17, W18])
    fuera = jer.expandir([MES], "2026-04-01", "2026-04-30", tope=3)
    assert [c.id for c in fuera] == [MES.id]


def test_expande_cuando_los_hijos_caben_y_cubren():
    """Con ventana estrecha bastan pocos hijos, y ahí sí se gana `span`."""
    jer = _jer([W15, W16, W17, W18])
    fuera = jer.expandir([MES], "2026-04-13", "2026-04-17", tope=3)
    assert [c.id for c in fuera] == [W16.id]


def test_sin_fecha_no_se_expande():
    """Cambiar un chunk por cuatro sin que nadie preguntara por una fecha añade
    ruido y no gana precisión."""
    jer = _jer([W15, W16, W17, W18])
    assert jer.expandir([MES], None, None) == [MES]


def test_un_padre_sin_hijos_se_queda_tal_cual():
    jer = _jer([])
    assert jer.expandir([MES], "2026-04-01", "2026-04-30") == [MES]


def test_la_ficha_no_se_expande():
    ficha = Chunk(id="perfil:aji", tipo=TIPO_PRODUCTO_PERFIL, texto="ficha",
                  slug="aji", producto="aji", mercado="GMML",
                  fecha_inicio="2024-07-01", fecha_fin="2026-08-24")
    jer = _jer([W15, W16, W17, W18])
    assert jer.expandir([ficha], "2026-04-01", "2026-04-30") == [ficha]


# --------------------------------------------------------------------------- #
# Deduplicación
# --------------------------------------------------------------------------- #
def test_el_padre_sobrevive_si_los_hijos_no_lo_cubren_entero():
    """La versión anterior lo tiraba en cuanto veía UN hijo, perdiendo 23 días.

    Costó 13 puntos de recall de búsqueda y 18 de MRR. Deduplicar no puede
    perder cobertura: para eso no es deduplicar, es borrar.
    """
    fuera = deduplicar([MES, W16])
    assert MES.id in {c.id for c in fuera}


def test_el_padre_se_va_si_los_hijos_lo_cubren_entero():
    """Con una jerarquía DE VERDAD: los días sí anidan en la semana."""
    dias = [periodo(f"p:aji:2026-04-{d}", "aji", f"2026-04-{d}", f"2026-04-{d}")
            for d in ("13", "14", "15", "16", "17")]
    fuera = deduplicar([W16, *dias])
    ids = {c.id for c in fuera}
    assert W16.id not in ids
    assert len(ids) == 5


def test_las_semanas_no_teselan_un_mes_y_por_eso_el_mes_sobrevive():
    """EL HALLAZGO ESTRUCTURAL, fijado como prueba.

    «mes -> semana» NO es una jerarquía: la semana del 30 de marzo al 3 de abril
    cruza el límite del mes, así que ninguna semana cubre el 1 y el 2 de abril y
    la unión de las semanas de abril nunca cubre abril entero.

    Un esquema padre/hijo exige que la partición hija REFINE a la del padre. Los
    días refinan la semana y el mes; las semanas no refinan el mes. Por eso la
    jerarquía por defecto es semana -> día y no mes -> semana.
    """
    fuera = deduplicar([MES, W15, W16, W17, W18])
    assert MES.id in {c.id for c in fuera}


def test_quita_el_mismo_chunk_repetido():
    assert [c.id for c in deduplicar([W16, W16])] == [W16.id]


def test_quita_texto_identico_con_id_distinto():
    """Un producto con un solo día de dato genera el mismo texto en semana y en
    mes: el id difiere y el filtro por id no lo ve."""
    a = periodo("p:x:2025-W01", "x", "2025-01-06", "2025-01-10", texto="mismo")
    b = periodo("p:x:2025-01", "x", "2025-01-06", "2025-01-10", texto="mismo")
    assert len(deduplicar([a, b])) == 1


def test_no_mezcla_productos_distintos():
    otro = periodo("p:otro:2026-W16", "otro", "2026-04-13", "2026-04-17")
    fuera = deduplicar([MES, otro])
    assert {c.id for c in fuera} == {MES.id, otro.id}


def test_conserva_el_orden():
    fuera = deduplicar([W18, W15, W16])
    assert [c.id for c in fuera] == [W18.id, W15.id, W16.id]
