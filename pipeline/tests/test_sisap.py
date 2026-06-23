"""Shape tests for the SISAP cross-check source.

These run offline when a cached sisap_*.pdf exists under data/raw/; otherwise they
fetch once and skip cleanly if there is no network (CI / air-gapped dev).
"""
from __future__ import annotations

import glob
import os
from datetime import date

import pytest

from preciovivo import sisap

RAW_DIR = os.environ.get("PRECIOVIVO_RAW", os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "raw"))

REQUIRED_KEYS = {
    "mercado", "producto", "precio_hoy_kg", "precio_ayer_kg",
    "prom_mes", "prom_7d", "fecha",
}


def _cached_pdf() -> str | None:
    hits = sorted(glob.glob(os.path.join(RAW_DIR, "sisap_*.pdf")))
    return hits[-1] if hits else None


@pytest.fixture(scope="module")
def sisap_pdf() -> str:
    """Path to a SISAP PDF: cached if available, else fetched, else skip."""
    cached = _cached_pdf()
    if cached:
        return cached
    try:
        path, _sha, _n = sisap.fetch_sisap()
    except Exception as e:  # network down / source unreachable
        pytest.skip(f"sin PDF SISAP cacheado y sin red: {e}")
    return path


@pytest.fixture(scope="module")
def rows(sisap_pdf) -> list[dict]:
    return sisap.parse_sisap(sisap_pdf)


def test_parse_returns_rows(rows):
    assert isinstance(rows, list) and len(rows) >= 10


def test_row_shape(rows):
    for r in rows:
        assert set(r.keys()) == REQUIRED_KEYS
        assert isinstance(r["mercado"], str) and r["mercado"]
        assert isinstance(r["producto"], str) and r["producto"]
        assert isinstance(r["fecha"], date)
        for k in ("precio_hoy_kg", "precio_ayer_kg"):
            assert r[k] is None or isinstance(r[k], float)


def test_only_known_mercados(rows):
    assert {r["mercado"] for r in rows} <= set(sisap.MERCADOS)


def test_prices_are_per_kg_and_sane(rows):
    # SISAP prices are already S/ por Kg; mayorista produce sits well under S/60.
    for r in rows:
        for k in ("precio_hoy_kg", "precio_ayer_kg", "prom_mes", "prom_7d"):
            v = r[k]
            assert v is None or 0.05 <= v <= 60, f"{r['producto']} {k}={v}"


def test_pollo_vivo_present(rows):
    pollos = [r for r in rows if "POLLO" in sisap._norm(r["producto"])]
    assert pollos, "se esperaba POLLO VIVO en el reporte SISAP"
    p = pollos[0]
    assert p["mercado"] == "MERCADO MAYORISTA DE AVES VIVAS"
    assert p["precio_hoy_kg"] and p["precio_hoy_kg"] > 1.0


def test_gmml_rows_exist(rows):
    gmml = [r for r in rows if r["mercado"] == sisap.GMML_LABEL]
    assert len(gmml) >= 5


def test_split_mercado_longest_prefix():
    # The long fruit-market label must not be shadowed by a shorter partial.
    mer, prod = sisap._split_mercado("MERCADO MAYORISTA NRO 2-FRUTAS PAPAYA")
    assert mer == "MERCADO MAYORISTA NRO 2-FRUTAS"
    assert prod == "PAPAYA"


def test_cross_check_shape(rows):
    cc = sisap.cross_check(rows)
    # Cross-check is GMML-only.
    assert all(isinstance(c["producto"], str) for c in cc)
    for c in cc:
        assert {"producto", "fecha", "sisap_kg", "gmml_kg",
                "delta_pct", "flag", "detalle"} <= set(c.keys())
        assert isinstance(c["flag"], bool)


def test_cross_check_missing_db_flags_all(rows):
    # With a non-existent DB, every GMML producto has no counterpart -> flagged.
    cc = sisap.cross_check(rows, db_path="/no/such/preciovivo.db")
    gmml = [r for r in rows if r["mercado"] == sisap.GMML_LABEL]
    assert len(cc) == len(gmml)
    assert all(c["flag"] and c["gmml_kg"] is None for c in cc)
