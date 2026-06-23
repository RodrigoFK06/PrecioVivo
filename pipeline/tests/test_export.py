"""Tests del exportador a snapshot.json (módulo preciovivo.export).

Solo importamos y testeamos: no modificamos export.py.

Cubre:
  - `slugify`: estable, sin acentos/ñ, idempotente.
  - `build()`: produce la FORMA del contrato base de snapshot a partir de una
    DB SQLite poblada (sin tocar la base real ni escribir archivos).
"""
from __future__ import annotations

from datetime import date

import pytest

from preciovivo import export
from preciovivo.export import build, slugify

FUENTE = "reporte-335"


# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #
class TestSlugify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Papa Blanca", "papa-blanca"),
            ("Limón", "limon"),                 # quita acento
            ("Ñame Criollo", "name-criollo"),   # ñ -> n
            ("Maíz Amarillo Duro", "maiz-amarillo-duro"),
            ("Ají/Páprika", "aji-paprika"),     # separador no alfanumérico -> '-'
            ("  Tomate  Italiano  ", "tomate-italiano"),  # colapsa espacios y recorta
            ("Café con Leche", "cafe-con-leche"),
        ],
    )
    def test_slugify_values(self, raw, expected):
        assert slugify(raw) == expected

    def test_slugify_is_idempotent(self):
        once = slugify("Plátano de Seda")
        assert slugify(once) == once  # aplicar slugify a un slug no lo cambia

    def test_slugify_lowercase_and_ascii(self):
        s = slugify("ÁRBOL Ñandú")
        assert s == s.lower()
        assert s.isascii()


# --------------------------------------------------------------------------- #
# build() : forma del contrato
# --------------------------------------------------------------------------- #
@pytest.fixture
def populated_db(temp_store, make_row):
    """Puebla el SQLite temporal con 2 productos x 2 fechas y devuelve la ruta."""
    f1, f2 = date(2026, 6, 21), date(2026, 6, 22)
    temp_store.upsert_precios(
        f1,
        [make_row("Papa Blanca", precio_hoy_unit=2.00, equiv_kg=1.0, masa_hoy=100.0),
         make_row("Limón Sutil", precio_hoy_unit=4.00, equiv_kg=1.0, masa_hoy=20.0)],
        FUENTE)
    temp_store.upsert_precios(
        f2,
        [make_row("Papa Blanca", precio_hoy_unit=2.50, precio_ayer_unit=2.00,
                  equiv_kg=1.0, masa_hoy=110.0, tendencia="En Alza"),
         make_row("Limón Sutil", precio_hoy_unit=3.60, precio_ayer_unit=4.00,
                  equiv_kg=1.0, masa_hoy=18.0, tendencia="En Baja")],
        FUENTE)
    # backend == "sqlite:<ruta>"
    return temp_store.backend.split("sqlite:", 1)[1]


class TestBuild:
    def test_top_level_shape(self, populated_db):
        snap = build(populated_db)

        # Claves de nivel raíz del contrato base.
        for key in ("generatedAt", "mercado", "latestFecha", "fechas",
                    "productCount", "attribution", "productos"):
            assert key in snap, f"falta clave de nivel raíz: {key}"

        assert snap["latestFecha"] == "2026-06-22"
        assert snap["fechas"] == ["2026-06-21", "2026-06-22"]
        assert snap["productCount"] == 2
        assert len(snap["productos"]) == 2
        assert "GMML" in snap["mercado"]
        assert isinstance(snap["attribution"], str) and snap["attribution"]

    def test_products_sorted_by_name(self, populated_db):
        snap = build(populated_db)
        nombres = [p["nombre"] for p in snap["productos"]]
        assert nombres == sorted(nombres)

    def test_product_shape(self, populated_db):
        snap = build(populated_db)
        prod = next(p for p in snap["productos"] if p["nombre"] == "Papa Blanca")

        # Campos por producto del contrato.
        for key in ("nombre", "slug", "categoria", "unidad", "equiv_kg",
                    "series", "latest"):
            assert key in prod, f"falta clave de producto: {key}"

        assert prod["slug"] == "papa-blanca"
        assert prod["unidad"] == "Kilogramo"
        assert prod["equiv_kg"] == pytest.approx(1.0)

        # Serie ordenada por fecha, una entrada por fecha.
        assert len(prod["series"]) == 2
        assert [s["fecha"] for s in prod["series"]] == ["2026-06-21", "2026-06-22"]
        for pt in prod["series"]:
            for key in ("fecha", "precio_kg", "masa_hoy", "masa_7d", "tendencia"):
                assert key in pt
            # El campo interno _ayer_kg debe haberse limpiado.
            assert "_ayer_kg" not in pt

    def test_latest_block(self, populated_db):
        snap = build(populated_db)
        prod = next(p for p in snap["productos"] if p["nombre"] == "Papa Blanca")
        latest = prod["latest"]

        for key in ("fecha", "precio_kg", "precio_ayer_kg", "var_pct",
                    "masa_hoy", "masa_7d", "tendencia"):
            assert key in latest, f"falta clave de latest: {key}"

        assert latest["fecha"] == "2026-06-22"
        assert latest["precio_kg"] == pytest.approx(2.50)
        assert latest["precio_ayer_kg"] == pytest.approx(2.00)
        # var_pct = 100 * (2.50 - 2.00) / 2.00 = 25.0
        assert latest["var_pct"] == pytest.approx(25.0)
        assert latest["tendencia"] == "En Alza"

    def test_var_pct_negative_for_drop(self, populated_db):
        snap = build(populated_db)
        prod = next(p for p in snap["productos"] if p["nombre"] == "Limón Sutil")
        # 100 * (3.60 - 4.00) / 4.00 = -10.0
        assert prod["latest"]["var_pct"] == pytest.approx(-10.0)
        assert prod["slug"] == "limon-sutil"  # acento removido en el slug

    def test_attribution_constant_exposed(self):
        # El texto legal de atribución es un hecho, no inventado.
        assert "MIDAGRI" in export.ATTRIBUTION
        assert "no oficiales" in export.ATTRIBUTION

    def test_build_empty_db_is_safe(self, temp_store):
        # DB vacía (esquema creado, sin precios) -> snapshot coherente y vacío.
        db_path = temp_store.backend.split("sqlite:", 1)[1]
        snap = build(db_path)
        assert snap["fechas"] == []
        assert snap["latestFecha"] is None
        assert snap["productos"] == []
        assert snap["productCount"] == 0
