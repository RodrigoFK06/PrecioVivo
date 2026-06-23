"""Fixtures compartidas para la suite de Precio Vivo.

Hacemos los tests robustos al directorio de ejecución: las rutas de datos
(la base SQLite temporal, el PDF de muestra) se resuelven respecto a la raíz
del paquete `pipeline/`, no respecto al cwd de pytest.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from preciovivo.parser import ProductRow

# pipeline/  (este archivo vive en pipeline/tests/conftest.py)
PIPELINE_DIR = Path(__file__).resolve().parent.parent
# Raíz del proyecto y carpeta de PDFs crudos cacheados.
DATA_RAW = PIPELINE_DIR.parent / "data" / "raw"
SAMPLE_PDF = DATA_RAW / "reporte-335_2026-06-22.pdf"


@pytest.fixture
def sample_pdf() -> Path | None:
    """Ruta al PDF de muestra del 2026-06-22, o None si no está cacheado."""
    return SAMPLE_PDF if SAMPLE_PDF.exists() else None


@pytest.fixture
def char_band():
    """Construye una banda de chars (como la que entrega pdfplumber) a partir
    de un string 'pegado' precio_7d+tendencia, p.ej. '4.E1s8table'.

    Cada char lleva su propia x0 creciente para que el orden por x sea el del
    string original, que es justo lo que hace `_split_price_trend`.
    """
    def _build(glued: str, x_start: float = 100.0, step: float = 4.0):
        return [
            {"text": ch, "x0": x_start + i * step, "top": 0.0}
            for i, ch in enumerate(glued)
        ]
    return _build


@pytest.fixture
def make_row():
    """Fábrica de ProductRow sintéticos. Solo `producto` es obligatorio; el
    resto trae valores sanos por defecto. equiv_kg distinto de None hace que
    __post_init__ derive los precios por kg automáticamente.
    """
    def _make(producto: str = "Papa Sintetica", **kw) -> ProductRow:
        base = dict(
            masa_ayer=100.0, masa_hoy=110.0, masa_7d=105.0, masa_4lun=108.0,
            unidad="Kilogramo", equiv_kg=1.0,
            precio_ayer_unit=2.00, precio_hoy_unit=2.50, precio_7d_unit=2.20,
            tendencia="Estable",
        )
        base.update(kw)
        return ProductRow(producto=producto, **base)
    return _make


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Store SQLite contra un archivo temporal aislado.

    Forzamos backend SQLite (limpiando DATABASE_URL) y apuntamos PRECIOVIVO_DB
    al tmp_path del test, tal como pide el contrato. El esquema queda inicializado.
    """
    from preciovivo.store import Store

    db_file = tmp_path / "test_preciovivo.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PRECIOVIVO_DB", str(db_file))

    st = Store()
    st.init_schema()
    try:
        yield st
    finally:
        st.close()
