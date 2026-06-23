"""Tests de la capa de almacenamiento (módulo preciovivo.store).

Solo importamos y testeamos: no modificamos store.py. Usamos un SQLite
temporal vía el fixture `temp_store` (env PRECIOVIVO_DB apuntando a tmp).

Foco: idempotencia de `upsert_precios` sobre la clave (producto, mercado, fecha).
"""
from __future__ import annotations

from datetime import date

import pytest

FECHA = date(2026, 6, 22)
FUENTE = "reporte-335"


def _count_precios(store) -> int:
    return store.count("precios_diarios")


def _fetch_precio(store, producto: str, fecha: date):
    """Devuelve la fila de precios_diarios de un producto/fecha como dict-ish."""
    ph = store.ph
    with store._cursor() as cur:
        cur.execute(
            f"SELECT pd.precio_hoy_unit, pd.precio_hoy_kg, pd.masa_hoy, pd.tendencia "
            f"FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            f"WHERE p.nombre_canonico = {ph} AND pd.fecha = {ph}",
            (producto, fecha.isoformat()),
        )
        return cur.fetchone()


class TestSchema:
    def test_init_creates_tables_and_gmml(self, temp_store):
        # init_schema (vía fixture) crea tablas y siembra el mercado GMML.
        assert temp_store.count("mercados") == 1
        assert temp_store.count("precios_diarios") == 0
        mid = temp_store.mercado_id("GMML", "Gran Mercado Mayorista de Lima")
        assert isinstance(mid, int) and mid > 0

    def test_backend_is_temp_sqlite(self, temp_store):
        # Confirmamos que NO estamos pegándole a Postgres ni a la DB real.
        assert temp_store.is_pg is False
        assert temp_store.ph == "?"
        assert temp_store.backend.startswith("sqlite:")
        assert "test_preciovivo.db" in temp_store.backend


class TestUpsertIdempotency:
    def test_two_upserts_same_rows_keep_one_row_per_product_fecha(self, temp_store, make_row):
        rows = [
            make_row("Papa Blanca", precio_hoy_unit=2.50),
            make_row("Limon Sutil", precio_hoy_unit=4.00),
            make_row("Tomate Italiano", precio_hoy_unit=3.20),
        ]

        n1 = temp_store.upsert_precios(FECHA, rows, FUENTE)
        assert n1 == 3
        assert _count_precios(temp_store) == 3

        # Segundo upsert de las MISMAS filas y MISMA fecha: idempotente.
        n2 = temp_store.upsert_precios(FECHA, rows, FUENTE)
        assert n2 == 3                       # procesa las 3 filas otra vez
        assert _count_precios(temp_store) == 3  # pero NO duplica

        # Productos tampoco se duplican.
        assert temp_store.count("productos") == 3

    def test_upsert_updates_existing_values(self, temp_store, make_row):
        # Inserción inicial.
        temp_store.upsert_precios(
            FECHA, [make_row("Cebolla Roja", precio_hoy_unit=1.00, masa_hoy=50.0,
                             tendencia="Estable")], FUENTE)
        before = _fetch_precio(temp_store, "Cebolla Roja", FECHA)
        assert before is not None
        assert before[0] == pytest.approx(1.00)   # precio_hoy_unit
        assert before[2] == pytest.approx(50.0)   # masa_hoy
        assert before[3] == "Estable"

        # Re-upsert con valores cambiados para el MISMO (producto, fecha).
        temp_store.upsert_precios(
            FECHA, [make_row("Cebolla Roja", precio_hoy_unit=1.75, masa_hoy=80.0,
                             tendencia="En Alza")], FUENTE)

        assert _count_precios(temp_store) == 1    # sigue habiendo una sola fila
        after = _fetch_precio(temp_store, "Cebolla Roja", FECHA)
        assert after[0] == pytest.approx(1.75)    # precio actualizado
        assert after[2] == pytest.approx(80.0)    # masa actualizada
        assert after[3] == "En Alza"              # tendencia actualizada

    def test_different_fechas_coexist(self, temp_store, make_row):
        # La fecha es parte de la PK: dos fechas del mismo producto coexisten.
        f1, f2 = date(2026, 6, 21), date(2026, 6, 22)
        temp_store.upsert_precios(f1, [make_row("Zanahoria")], FUENTE)
        temp_store.upsert_precios(f2, [make_row("Zanahoria")], FUENTE)

        assert _count_precios(temp_store) == 2
        assert temp_store.count("productos") == 1  # mismo producto canónico
        assert sorted(temp_store.distinct_fechas()) == [f1.isoformat(), f2.isoformat()]

    def test_precio_kg_derived_and_stored(self, temp_store, make_row):
        # equiv_kg=2.0 -> precio_hoy_kg = precio_hoy_unit / 2.
        row = make_row("Sandia Negra", precio_hoy_unit=8.00, equiv_kg=2.0)
        assert row.precio_hoy_kg == pytest.approx(4.00)  # derivado en __post_init__
        temp_store.upsert_precios(FECHA, [row], FUENTE)
        stored = _fetch_precio(temp_store, "Sandia Negra", FECHA)
        assert stored[1] == pytest.approx(4.00)          # precio_hoy_kg persistido
