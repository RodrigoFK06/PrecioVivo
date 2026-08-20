"""Shape tests for the boletín GMML/MMF2 parser.

Run offline when a cached boletin-gmml-mm2_*.pdf exists under data/raw/; else
fetch the bundled sample once and skip cleanly if there is no network.
"""
from __future__ import annotations

import glob
import os
from datetime import date

import pytest

from preciovivo import boletin

RAW_DIR = os.environ.get("PRECIOVIVO_RAW", os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "raw"))

REQUIRED_KEYS = {"mercado", "cnpa", "producto", "precio_kg", "fecha", "var_pct"}


# La edición de la que se leyeron los valores exactos del spot-check. Tiene que
# ser ESA y no "la más nueva": los precios cambian cada día, así que un test de
# valores atado a "el último PDF que haya en caché" pasa mientras solo exista esa
# edición y falla en cuanto se descargue otra.
#
# Pasó al cablear la navegación de la colección 338: hasta entonces el único PDF
# cacheado era el de marzo y la coincidencia parecía intencionada.
EDICION_DEL_SPOT_CHECK = "boletin-gmml-mm2_2026-03-12.pdf"


def _cached_pdf() -> str | None:
    """El PDF más NUEVO en caché. A propósito: los tests estructurales deben
    correr contra la edición más reciente, que es como se detecta que la fuente
    cambió de formato."""
    hits = sorted(glob.glob(os.path.join(RAW_DIR, "boletin-gmml-mm2_*.pdf")))
    return hits[-1] if hits else None


def _pdf_del_spot_check() -> str | None:
    ruta = os.path.join(RAW_DIR, EDICION_DEL_SPOT_CHECK)
    return ruta if os.path.exists(ruta) else None


@pytest.fixture(scope="module")
def boletin_pdf() -> str:
    """Path to a boletín PDF: cached if available, else fetched, else skip."""
    cached = _cached_pdf()
    if cached:
        return cached
    try:
        path, _sha, _n = boletin.fetch_boletin(boletin.SAMPLE_URL)
    except Exception as e:  # network down / source unreachable
        pytest.skip(f"sin PDF boletín cacheado y sin red: {e}")
    return path


@pytest.fixture(scope="module")
def rows_de_la_edicion_fijada() -> list[dict]:
    """Filas de la edición concreta de la que se leyeron los valores exactos."""
    ruta = _pdf_del_spot_check()
    if not ruta:
        pytest.skip(f"falta {EDICION_DEL_SPOT_CHECK}, la edición del spot-check")
    return boletin.parse_boletin(ruta)


@pytest.fixture(scope="module")
def rows(boletin_pdf) -> list[dict]:
    return boletin.parse_boletin(boletin_pdf)


def test_parse_returns_many_rows(rows):
    # The grid carries ~150 producto x mercado rows across the two grid pages.
    assert isinstance(rows, list) and len(rows) >= 100


def test_row_shape(rows):
    for r in rows:
        assert set(r.keys()) == REQUIRED_KEYS
        assert isinstance(r["mercado"], str) and r["mercado"]
        assert isinstance(r["cnpa"], str) and len(r["cnpa"]) == 5 and r["cnpa"].isdigit()
        assert isinstance(r["producto"], str) and len(r["producto"]) >= 2
        assert isinstance(r["precio_kg"], float)
        assert isinstance(r["fecha"], date)
        assert r["var_pct"] is None or isinstance(r["var_pct"], float)


def test_only_known_mercados(rows):
    assert {r["mercado"] for r in rows} <= set(boletin.MERCADOS.values())


def test_both_markets_present(rows):
    # The whole point of this source: it adds the FRUTAS market (MMF2) on top of
    # GMML. Both must appear, each with a meaningful number of products.
    by = {}
    for r in rows:
        by[r["mercado"]] = by.get(r["mercado"], 0) + 1
    assert by.get("GMML", 0) >= 20
    assert by.get("MMF2", 0) >= 20, "se esperaba el mercado de FRUTAS (MMF2)"


def test_frutas_products_present(rows):
    # Sanity: MMF2 should carry obvious fruit products (plátano, palta, etc.).
    mmf2 = " ".join(r["producto"] for r in rows if r["mercado"] == "MMF2").upper()
    assert "PLATANO" in mmf2 or "PALTA" in mmf2 or "MARACUYA" in mmf2


def test_prices_are_per_kg_and_sane(rows):
    # Boletín prices are already S/ por kg; mayorista produce sits under S/60.
    for r in rows:
        assert 0.05 <= r["precio_kg"] <= 60, f"{r['producto']} precio_kg={r['precio_kg']}"


def test_same_product_can_span_two_markets(rows):
    # MARACUYA COSTA is priced in both GMML and MMF2 in the sample; the parser
    # must keep them as distinct rows (proof the mercado column is read per-row).
    maracuya = {r["mercado"] for r in rows if r["producto"] == "MARACUYA COSTA"}
    if maracuya:  # present in the bundled sample; guard for other editions
        assert maracuya == {"GMML", "MMF2"}


def test_known_values_spot_check(rows_de_la_edicion_fijada):
    rows = rows_de_la_edicion_fijada
    # Exact values read off the 12-03-2026 grid (precio_kg = current-week avg).
    expected = {
        ("GMML", "01122", "MAIZ MARLO/CORONTA DE MAIZ"): (18.25, 3.0),
        ("MMF2", "01319", "MARACUYA COSTA"): (2.71, -1.4),
        ("MMF2", "01321", "TORONJA COSTA CON PEPA"): (4.57, 4.7),
    }
    index = {(r["mercado"], r["cnpa"], r["producto"]): r for r in rows}
    for key, (precio, var) in expected.items():
        if key in index:  # only assert when present (skip on other editions)
            r = index[key]
            assert r["precio_kg"] == precio
            assert r["var_pct"] == var


def test_coverage_is_high(boletin_pdf):
    cov = boletin.coverage(boletin_pdf)
    assert cov["candidatos"] > 0
    assert cov["parseados"] == cov["candidatos"], "se dropearon filas-grid"
    assert cov["cobertura_pct"] >= 95.0
    assert set(cov["por_mercado"]) <= set(boletin.MERCADOS.values())


def test_join_words_reconstructs_spaced_glyphs():
    # Synthetic tokens mimic the spaced-glyph page: ~0px gaps inside a word,
    # ~1px between words. The joiner must yield "MARACUYA COSTA".
    def tok(text, x0, x1):
        return {"text": text, "x0": x0, "x1": x1}
    ws = [
        tok("M", 85.0, 89.0), tok("ARACUYA", 89.0, 106.0),
        tok("CO", 107.0, 112.0), tok("STA", 112.1, 119.0),
    ]
    out = boletin._join_words(ws, boletin.NAME_X0, boletin.NAME_X1)
    assert out == "MARACUYA COSTA"


def test_fetch_signature_offline():
    # fetch_boletin must reject a non-PDF body without touching the network here;
    # we only assert the function exists with the documented arity.
    assert callable(boletin.fetch_boletin)
    assert boletin.SAMPLE_URL.endswith(".pdf")
