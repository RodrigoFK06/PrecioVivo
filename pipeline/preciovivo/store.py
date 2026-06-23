"""Storage layer for Precio Vivo.

Dual backend, one upsert path:
  - PostgreSQL / Supabase when env DATABASE_URL is set (the production target).
  - SQLite file otherwise (local dev, zero install).

The data-path SQL (INSERT ... ON CONFLICT ... DO UPDATE) is valid on both
engines; only the DDL and the parameter marker differ. `schema.sql` is the
canonical Postgres schema; the SQLite DDL here mirrors it.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone

GMML = ("GMML", "Gran Mercado Mayorista de Lima")

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS mercados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_canonico TEXT UNIQUE NOT NULL, categoria TEXT,
    unidad_default TEXT, equiv_kg REAL);
CREATE TABLE IF NOT EXISTS precios_diarios (
    fecha TEXT NOT NULL, mercado_id INTEGER NOT NULL, producto_id INTEGER NOT NULL,
    masa_ayer REAL, masa_hoy REAL, masa_7d REAL, masa_4lun REAL,
    unidad TEXT, equiv_kg REAL,
    precio_ayer_unit REAL, precio_hoy_unit REAL, precio_7d_unit REAL,
    precio_ayer_kg REAL, precio_hoy_kg REAL, precio_7d_kg REAL,
    tendencia TEXT, fuente TEXT, cargado_en TEXT NOT NULL,
    PRIMARY KEY (producto_id, mercado_id, fecha));
CREATE INDEX IF NOT EXISTS ix_precios_prod_fecha ON precios_diarios (producto_id, fecha);
CREATE INDEX IF NOT EXISTS ix_precios_fecha ON precios_diarios (fecha);
CREATE TABLE IF NOT EXISTS pronosticos (
    producto_id INTEGER NOT NULL, mercado_id INTEGER NOT NULL,
    fecha_generado TEXT NOT NULL, horizonte INTEGER NOT NULL,
    precio_estimado REAL, metodo TEXT, intervalo_lo REAL, intervalo_hi REAL, mae REAL,
    PRIMARY KEY (producto_id, mercado_id, fecha_generado, horizonte));
CREATE INDEX IF NOT EXISTS ix_pronosticos_prod ON pronosticos (producto_id, fecha_generado);
CREATE TABLE IF NOT EXISTS fuentes_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fuente TEXT NOT NULL, fecha TEXT, url TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER, descargado_en TEXT NOT NULL, UNIQUE (fuente, sha256));
"""

_PRECIO_COLS = [
    "fecha", "mercado_id", "producto_id",
    "masa_ayer", "masa_hoy", "masa_7d", "masa_4lun",
    "unidad", "equiv_kg",
    "precio_ayer_unit", "precio_hoy_unit", "precio_7d_unit",
    "precio_ayer_kg", "precio_hoy_kg", "precio_7d_kg",
    "tendencia", "fuente", "cargado_en",
]
_PRECIO_UPDATE = [c for c in _PRECIO_COLS if c not in ("fecha", "mercado_id", "producto_id")]

_PRONOSTICO_COLS = [
    "producto_id", "mercado_id", "fecha_generado", "horizonte",
    "precio_estimado", "metodo", "intervalo_lo", "intervalo_hi", "mae",
]
_PRONOSTICO_PK = ("producto_id", "mercado_id", "fecha_generado", "horizonte")
_PRONOSTICO_UPDATE = [c for c in _PRONOSTICO_COLS if c not in _PRONOSTICO_PK]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        self.is_pg = bool(self.dsn)
        self.ph = "%s" if self.is_pg else "?"
        if self.is_pg:
            import psycopg  # lazy
            self._conn = psycopg.connect(self.dsn)
        else:
            path = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self._conn = sqlite3.connect(path)
            self._conn.execute("PRAGMA foreign_keys=ON")
        self.backend = "postgres" if self.is_pg else f"sqlite:{path}"

    # --- lifecycle -------------------------------------------------------
    def init_schema(self):
        cur = self._conn.cursor()
        if self.is_pg:
            with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql"), encoding="utf-8") as f:
                cur.execute(f.read())
        else:
            cur.executescript(_SQLITE_DDL)
        self._conn.commit()
        self.mercado_id(*GMML)

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def close(self):
        self._conn.close()

    # --- dimensions ------------------------------------------------------
    def mercado_id(self, codigo: str, nombre: str) -> int:
        with self._cursor() as cur:
            cur.execute(f"INSERT INTO mercados (codigo, nombre) VALUES ({self.ph},{self.ph}) "
                        f"ON CONFLICT (codigo) DO NOTHING", (codigo, nombre))
            cur.execute(f"SELECT id FROM mercados WHERE codigo={self.ph}", (codigo,))
            return cur.fetchone()[0]

    def producto_id(self, nombre: str, unidad: str | None, equiv_kg: float | None) -> int:
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO productos (nombre_canonico, unidad_default, equiv_kg) "
                f"VALUES ({self.ph},{self.ph},{self.ph}) "
                f"ON CONFLICT (nombre_canonico) DO UPDATE SET "
                f"unidad_default=excluded.unidad_default, equiv_kg=excluded.equiv_kg",
                (nombre, unidad, equiv_kg))
            cur.execute(f"SELECT id FROM productos WHERE nombre_canonico={self.ph}", (nombre,))
            return cur.fetchone()[0]

    # --- facts -----------------------------------------------------------
    def upsert_precios(self, fecha, rows, fuente: str, mercado_id: int | None = None) -> int:
        """rows: iterable of ProductRow. Idempotent on (producto, mercado, fecha)."""
        mid = mercado_id if mercado_id is not None else self.mercado_id(*GMML)
        fecha_s = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha)
        cargado = _now()
        cols = ", ".join(_PRECIO_COLS)
        marks = ", ".join([self.ph] * len(_PRECIO_COLS))
        sets = ", ".join(f"{c}=excluded.{c}" for c in _PRECIO_UPDATE)
        sql = (f"INSERT INTO precios_diarios ({cols}) VALUES ({marks}) "
               f"ON CONFLICT (producto_id, mercado_id, fecha) DO UPDATE SET {sets}")
        n = 0
        with self._cursor() as cur:
            for r in rows:
                pid = self.producto_id(r.producto, r.unidad, r.equiv_kg)
                cur.execute(sql, (
                    fecha_s, mid, pid,
                    r.masa_ayer, r.masa_hoy, r.masa_7d, r.masa_4lun,
                    r.unidad, r.equiv_kg,
                    r.precio_ayer_unit, r.precio_hoy_unit, r.precio_7d_unit,
                    r.precio_ayer_kg, r.precio_hoy_kg, r.precio_7d_kg,
                    r.tendencia, fuente, cargado,
                ))
                n += 1
        return n

    def upsert_pronosticos(self, fecha_generado, items, mercado_id: int | None = None) -> int:
        """Guarda pronósticos. Idempotente en (producto, mercado, fecha_generado, horizonte).

        `items`: iterable de (nombre_canonico, forecast_dict), donde forecast_dict
        sigue el contrato producto.forecast de forecast.py:
          {metodo, horizonte_dias, precio_estimado, intervalo:[lo,hi], mae_modelo, ...}
        El producto se resuelve/crea por nombre_canonico (no se reescriben unidad/equiv:
        se conservan si ya existe; si es nuevo, quedan en NULL).
        """
        mid = mercado_id if mercado_id is not None else self.mercado_id(*GMML)
        fecha_s = fecha_generado.isoformat() if hasattr(fecha_generado, "isoformat") else str(fecha_generado)
        cols = ", ".join(_PRONOSTICO_COLS)
        marks = ", ".join([self.ph] * len(_PRONOSTICO_COLS))
        sets = ", ".join(f"{c}=excluded.{c}" for c in _PRONOSTICO_UPDATE)
        conflict = ", ".join(_PRONOSTICO_PK)
        sql = (f"INSERT INTO pronosticos ({cols}) VALUES ({marks}) "
               f"ON CONFLICT ({conflict}) DO UPDATE SET {sets}")
        n = 0
        with self._cursor() as cur:
            for nombre, fc in items:
                pid = self._producto_id_existente(cur, nombre)
                interval = fc.get("intervalo") or [None, None]
                lo = interval[0] if len(interval) > 0 else None
                hi = interval[1] if len(interval) > 1 else None
                cur.execute(sql, (
                    pid, mid, fecha_s, int(fc.get("horizonte_dias", 1)),
                    fc.get("precio_estimado"), fc.get("metodo"),
                    lo, hi, fc.get("mae_modelo"),
                ))
                n += 1
        return n

    def _producto_id_existente(self, cur, nombre: str) -> int:
        """Resuelve producto_id por nombre_canonico, creándolo si no existe.

        A diferencia de `producto_id`, no pisa unidad_default/equiv_kg (el forecast
        no aporta esos campos). Reusa el cursor de la transacción en curso.
        """
        cur.execute(f"SELECT id FROM productos WHERE nombre_canonico={self.ph}", (nombre,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            f"INSERT INTO productos (nombre_canonico) VALUES ({self.ph}) "
            f"ON CONFLICT (nombre_canonico) DO NOTHING", (nombre,))
        cur.execute(f"SELECT id FROM productos WHERE nombre_canonico={self.ph}", (nombre,))
        return cur.fetchone()[0]

    def record_raw(self, fuente, fecha, url, sha256, nbytes):
        fecha_s = fecha.isoformat() if hasattr(fecha, "isoformat") else (fecha or None)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO fuentes_raw (fuente, fecha, url, sha256, bytes, descargado_en) "
                f"VALUES ({self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph}) "
                f"ON CONFLICT (fuente, sha256) DO NOTHING",
                (fuente, fecha_s, url, sha256, nbytes, _now()))

    # --- read helpers (for the dashboard/preview) ------------------------
    def count(self, table: str) -> int:
        with self._cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]

    def distinct_fechas(self):
        with self._cursor() as cur:
            cur.execute("SELECT DISTINCT fecha FROM precios_diarios ORDER BY fecha")
            return [r[0] for r in cur.fetchall()]
