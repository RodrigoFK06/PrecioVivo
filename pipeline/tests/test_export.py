"""Tests del exportador a snapshot.json (módulo preciovivo.export).

Solo importamos y testeamos: no modificamos export.py.

Cubre:
  - `slugify`: estable, sin acentos/ñ, idempotente.
  - `build()`: produce la FORMA del contrato base de snapshot a partir de una
    DB SQLite poblada (sin tocar la base real ni escribir archivos).
"""
from __future__ import annotations

import os
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


# --------------------------------------------------------------------------- #
# Paridad SQLite <-> Postgres
#
# `export.build` leía con `sqlite3.connect` directo mientras `Store` y
# `forecast` ya hablaban los dos backends: con DATABASE_URL se podía INGERIR y
# PRONOSTICAR pero no EXPORTAR, y el snapshot es la fuente de todo lo que leen
# el sitio, la API, el MCP, el agente y la CLI. El camino a producción de
# DEPLOY.md quedaba cortado justo ahí.
#
# La propiedad que importa no es "el export contra Postgres no revienta", sino
# que produzca EL MISMO artefacto: si los tipos de fecha o los números difieren
# entre backends, el sitio se rompe solo en producción. Por eso el test compara
# los dos snapshots campo por campo.
# --------------------------------------------------------------------------- #
ESQUEMA_TEST = "preciovivo_export_test"


def _dsn_en_esquema(dsn: str, esquema: str) -> str:
    """DSN que apunta a un esquema propio, para no tocar las tablas reales.

    Se usa `options=-c search_path=<esquema>` (parámetro de libpq) en vez de
    truncar tablas: el test es entonces NO destructivo sobre la base de destino,
    y el esquema entero se borra al terminar.
    """
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options=-c%20search_path%3D{esquema}"


def _poblar(store, make_row) -> None:
    """Los mismos dos productos x dos fechas, en el backend que sea."""
    f1, f2 = date(2026, 6, 21), date(2026, 6, 22)
    store.upsert_precios(
        f1,
        [make_row("Papa Blanca", precio_hoy_unit=2.00, equiv_kg=1.0, masa_hoy=100.0),
         make_row("Limón Sutil", precio_hoy_unit=4.00, equiv_kg=1.0, masa_hoy=20.0)],
        FUENTE)
    store.upsert_precios(
        f2,
        [make_row("Papa Blanca", precio_hoy_unit=2.50, precio_ayer_unit=2.00,
                  equiv_kg=1.0, masa_hoy=110.0, tendencia="En Alza"),
         make_row("Limón Sutil", precio_hoy_unit=3.60, precio_ayer_unit=4.00,
                  equiv_kg=1.0, masa_hoy=18.0, tendencia="En Baja")],
        FUENTE)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                    reason="requiere un Postgres con el esquema (DATABASE_URL)")
def test_export_en_postgres_da_el_mismo_snapshot_que_sqlite(tmp_path, make_row, monkeypatch):
    import psycopg

    from preciovivo.store import Store

    dsn_base = os.environ["DATABASE_URL"]

    # --- SQLite: la referencia -------------------------------------------
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PRECIOVIVO_DB", str(tmp_path / "ref.db"))
    st_lite = Store()
    st_lite.init_schema()
    _poblar(st_lite, make_row)
    st_lite.close()
    snap_lite = build(str(tmp_path / "ref.db"))

    # --- Postgres: en un esquema propio y desechable ----------------------
    with psycopg.connect(dsn_base, autocommit=True) as con:
        con.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA_TEST} CASCADE")
        con.execute(f"CREATE SCHEMA {ESQUEMA_TEST}")

    monkeypatch.setenv("DATABASE_URL", _dsn_en_esquema(dsn_base, ESQUEMA_TEST))
    try:
        st_pg = Store()
        st_pg.init_schema()
        _poblar(st_pg, make_row)
        st_pg.close()
        # `db_path` se ignora en Postgres, pero se pasa uno temporal para que los
        # caches auxiliares (forecast, sisap) no toquen los del repo.
        snap_pg = build(str(tmp_path / "pg-ignorado.db"))
    finally:
        monkeypatch.setenv("DATABASE_URL", dsn_base)
        with psycopg.connect(dsn_base, autocommit=True) as con:
            con.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA_TEST} CASCADE")

    # --- El artefacto tiene que ser el mismo ------------------------------
    assert snap_pg["latestFecha"] == snap_lite["latestFecha"] == "2026-06-22"
    assert snap_pg["fechas"] == snap_lite["fechas"] == ["2026-06-21", "2026-06-22"]
    assert snap_pg["productCount"] == snap_lite["productCount"] == 2
    # Comparación campo por campo: aquí es donde saltaría un `date` de Postgres
    # colándose donde SQLite devuelve `str`, que es el fallo que rompería el
    # sitio solo en producción.
    assert snap_pg["productos"] == snap_lite["productos"]
    for p in snap_pg["productos"]:
        assert isinstance(p["latest"]["fecha"], str)
        assert all(isinstance(pt["fecha"], str) for pt in p["series"])
