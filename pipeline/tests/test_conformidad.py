"""El fixture de conformidad tiene que reflejar el `retrieval.py` de HOY.

Este test es la mitad Python del contrato con `web/lib/rag.ts`. No comprueba que
el sitio esté bien —eso lo hace `web/test/conformidad.test.ts`—, comprueba que
el fixture no se haya quedado viejo: si alguien cambia el parseo o BM25 y no
regenera, el contrato dejaría de describir la implementación de referencia y el
test del sitio pasaría a validar contra una foto antigua.

El ciclo completo, entonces:
  1. cambias `retrieval.py`               -> este test falla
  2. regeneras el fixture                 -> este test pasa
  3. `rag.ts` ya no reproduce el fixture  -> el vitest falla
  4. alineas `rag.ts`                     -> pasa todo

Que es exactamente lo que la deuda declarada del README pedía y no existía.
"""
from __future__ import annotations

import json

import pytest

from tests.conformidad import DESTINO, construir


@pytest.fixture
def en_disco() -> dict:
    if not DESTINO.exists():
        pytest.fail(
            f"No existe {DESTINO}. Genéralo con:\n"
            f"    cd pipeline && python tests/conformidad.py")
    return json.loads(DESTINO.read_text(encoding="utf-8"))


def test_el_fixture_esta_al_dia(en_disco):
    actual = construir()
    if en_disco == actual:
        return

    # Mensaje útil: decir QUÉ sección cambió ahorra abrir un diff de 300 líneas.
    cambiadas = [k for k in actual if en_disco.get(k) != actual[k]]
    detalle = []
    for seccion in ("parseo", "bm25", "rrf"):
        if seccion not in cambiadas:
            continue
        viejo, nuevo = en_disco.get(seccion) or [], actual[seccion]
        for a, b in zip(viejo, nuevo, strict=False):
            if a != b:
                clave = b.get("pregunta", b.get("listas"))
                detalle.append(f"  [{seccion}] {clave!r}\n     antes: {a}\n     ahora: {b}")
                break
    pytest.fail(
        "El fixture de conformidad quedó obsoleto: `retrieval.py` cambió y "
        "web/test/fixtures/conformidad.json ya no lo describe.\n"
        f"Secciones distintas: {cambiadas}\n"
        + "\n".join(detalle[:5])
        + "\n\nSi el cambio es deliberado, regenera y revisa el diff:\n"
          "    cd pipeline && python tests/conformidad.py\n"
          "Eso hará fallar web/test/conformidad.test.ts hasta que rag.ts lo alcance, "
          "que es el punto.")


def test_el_contrato_cubre_las_trampas_del_dominio(en_disco):
    """Un contrato sin los casos difíciles no contrata nada.

    Estos cuatro son los que de verdad pueden divergir entre dos
    implementaciones, y los tres primeros ya fueron bugs conceptuales reales.
    """
    por_pregunta = {c["pregunta"]: c for c in en_disco["parseo"]}

    # 1. "papa" NO puede arrastrar "papaya": match por token, nunca subcadena.
    papa = por_pregunta["¿a cuánto está la papa?"]
    assert "papaya" not in papa["slugs"]
    assert len(papa["slugs"]) >= 2, "una pregunta genérica agrupa la familia"

    # 2. Dos tokens identifican una variedad y NO expanden a la familia.
    especifico = por_pregunta["compara ajo morado con ajo criollo"]
    assert set(especifico["slugs"]) == {"ajo-morado", "ajo-criollo-o-napuri"}

    # 3. Una pregunta estacional nombra un mes pero NO debe acotar el rango.
    est = por_pregunta["¿cuánto suele costar la zanahoria en agosto?"]
    assert est["estacional"] is True
    assert est["desde"] is None and est["hasta"] is None

    # 4. Un día concreto gana al patrón de mes (si no, se ensancharía a julio).
    dia = por_pregunta["¿cuánto costó el tomate el 15 de julio?"]
    assert dia["desde"] == dia["hasta"] == "2026-07-15"


def test_las_fechas_se_resuelven_contra_el_dato_no_contra_el_reloj(en_disco):
    """`fecha_ref` es la del último dato. Si el pipeline no corrió hoy, "esta
    semana" tiene que ser la última semana CON DATOS, no una ventana vacía del
    calendario. Un fixture anclado al reloj se pudriría solo."""
    ref = en_disco["fecha_ref"]
    esta_semana = next(c for c in en_disco["parseo"]
                       if c["pregunta"] == "¿por qué subió la papa esta semana?")
    assert esta_semana["hasta"] == ref
    assert esta_semana["desde"] <= ref
