"""Pruebas de la capa IA (preciovivo.ai).

Auto-contenidas: NO requieren clave de API ni red. Verifican que:
  - sin ANTHROPIC_API_KEY todo cae al fallback determinista (fuente='fallback');
  - nl_query solo produce SELECT de solo lectura (y is_safe_select rechaza escrituras);
  - detecta_anomalias devuelve la forma del contrato snapshot.anomalias;
  - la detección es estadística pura (sin LLM) e inyecta una anomalía sintética.
"""
from __future__ import annotations

import os

import pytest

from preciovivo import ai


# --------------------------------------------------------------------------- #
# Fixture: garantiza que NO hay clave de API durante las pruebas (modo fallback)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _sin_api_key(monkeypatch):
    # Garantiza modo fallback: borra TODAS las claves posibles (el proveedor IA
    # es OpenAI-compatible; ai.py además carga .env, que en local trae la clave).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Datos sintéticos
# --------------------------------------------------------------------------- #
def _series_planas(n=40, precio=5.0, masa=100.0):
    """Una serie 'normal' (sin anomalías) para un par de productos."""
    fechas = [f"2026-05-{(i % 28) + 1:02d}" for i in range(n)]
    return {
        "Tomate": {"fechas": list(fechas), "precio": [precio] * n, "masa": [masa] * n},
        "Cebolla": {"fechas": list(fechas), "precio": [precio + 0.5] * n,
                    "masa": [masa + 10] * n},
    }


# --------------------------------------------------------------------------- #
# Fallback obligatorio sin clave
# --------------------------------------------------------------------------- #
def test_daily_summary_cae_a_fallback():
    facts = {
        "fecha": "2026-06-23",
        "mercado": "GMML",
        "n_productos": 72,
        "subas": [{"nombre": "Limón", "var_pct": 12.3, "precio_kg": 4.10}],
        "bajas": [{"nombre": "Papa", "var_pct": -8.0, "precio_kg": 1.20}],
        "ingreso_total_t": 1234.5,
        "ingreso_var_pct": 3.2,
    }
    r = ai.daily_summary(facts)
    assert r["fuente"] == "fallback"
    assert isinstance(r["texto"], str) and r["texto"]
    # El fallback debe narrar los hechos entregados, no inventar.
    assert "Limón" in r["texto"]
    assert "Papa" in r["texto"]


def test_daily_summary_tolera_facts_vacios():
    r = ai.daily_summary({})
    assert r["fuente"] == "fallback"
    assert isinstance(r["texto"], str) and r["texto"]


def test_narrate_anomalies_cae_a_fallback():
    anoms = [{
        "slug": "tomate", "nombre": "Tomate", "tipo": "precio",
        "fecha": "2026-06-23", "z": 4.8, "detalle": "precio S/ 9.00/kg",
    }]
    r = ai.narrate_anomalies(anoms)
    assert r["fuente"] == "fallback"
    assert "Tomate" in r["texto"]


def test_narrate_anomalies_vacio():
    r = ai.narrate_anomalies([])
    assert r["fuente"] == "fallback"
    assert isinstance(r["texto"], str) and r["texto"]


def test_nl_query_cae_a_fallback_y_es_select():
    r = ai.nl_query("¿Cuáles son los productos más caros hoy?")
    assert r["fuente"] == "fallback"
    assert ai.is_safe_select(r["sql"])


# --------------------------------------------------------------------------- #
# nl_query: solo SELECT de solo lectura
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("preg", [
    "productos más caros",
    "productos más baratos",
    "mayor volumen de ingreso",
    "qué está en alza",
    "una pregunta cualquiera sin palabra clave",
])
def test_nl_query_siempre_devuelve_select_seguro(preg):
    r = ai.nl_query(preg)
    sql = r["sql"]
    assert ai.is_safe_select(sql)
    assert sql.lstrip().lower().startswith("select")


@pytest.mark.parametrize("malo", [
    "DELETE FROM precios_diarios",
    "drop table productos",
    "INSERT INTO productos VALUES (1)",
    "UPDATE precios_diarios SET precio_hoy_kg = 0",
    "SELECT 1; DROP TABLE productos",          # múltiples sentencias
    "SELECT 1 -- ; DROP TABLE productos",      # comentario que oculta otra
    "PRAGMA table_info(productos)",
    "ATTACH DATABASE 'x.db' AS y",
    "WITH t AS (DELETE FROM productos RETURNING *) SELECT * FROM t",
    "",
    "   ",
])
def test_is_safe_select_rechaza_escrituras(malo):
    assert ai.is_safe_select(malo) is False


@pytest.mark.parametrize("bueno", [
    "SELECT * FROM productos",
    "select nombre_canonico from productos order by id",
    "SELECT * FROM productos;",                # un solo ';' final permitido
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
])
def test_is_safe_select_acepta_selects(bueno):
    assert ai.is_safe_select(bueno) is True


# --------------------------------------------------------------------------- #
# detecta_anomalias: contrato y comportamiento estadístico
# --------------------------------------------------------------------------- #
def test_detecta_anomalias_serie_plana_no_marca_nada():
    anoms = ai.detecta_anomalias(_series_planas())
    assert anoms == []


def test_detecta_anomalias_inyectada_devuelve_contrato():
    series = _series_planas()
    # Inyecta un salto fuerte de PRECIO en el último día del Tomate.
    series["Tomate"]["precio"][-1] = 30.0
    anoms = ai.detecta_anomalias(series)

    assert isinstance(anoms, list) and len(anoms) >= 1
    a = next(x for x in anoms if x["nombre"] == "Tomate" and x["tipo"] == "precio")
    # Forma exacta del contrato snapshot.anomalias.
    assert set(a.keys()) == {"slug", "nombre", "tipo", "fecha", "z", "detalle"}
    assert a["slug"] == "tomate"
    assert a["tipo"] in ("precio", "volumen")
    assert a["fecha"] == series["Tomate"]["fechas"][-1]
    assert isinstance(a["z"], (int, float)) and abs(a["z"]) >= ai.ANOMALIA_Z
    assert isinstance(a["detalle"], str) and a["detalle"]


def test_detecta_anomalias_volumen():
    series = _series_planas()
    series["Cebolla"]["masa"][-1] = 5000.0  # salto de volumen
    anoms = ai.detecta_anomalias(series)
    assert any(x["nombre"] == "Cebolla" and x["tipo"] == "volumen" for x in anoms)


def test_detecta_anomalias_ordenada_por_z():
    series = _series_planas()
    series["Tomate"]["precio"][-1] = 30.0
    series["Cebolla"]["precio"][-1] = 12.0
    anoms = ai.detecta_anomalias(series)
    zs = [abs(a["z"]) for a in anoms]
    assert zs == sorted(zs, reverse=True)


def test_detecta_anomalias_desde_snapshot():
    snap = {
        "productos": [{
            "nombre": "Tomate",
            "series": (
                [{"fecha": f"2026-05-{i+1:02d}", "precio_kg": 5.0, "masa_hoy": 100.0}
                 for i in range(20)]
                + [{"fecha": "2026-06-01", "precio_kg": 30.0, "masa_hoy": 100.0}]
            ),
        }],
    }
    anoms = ai.detecta_anomalias(snap)
    assert any(a["nombre"] == "Tomate" and a["tipo"] == "precio" for a in anoms)


def test_detecta_anomalias_tipo_origen_invalido():
    with pytest.raises(TypeError):
        ai.detecta_anomalias(12345)


# --------------------------------------------------------------------------- #
# Integración opcional con la BD local (si existe)
# --------------------------------------------------------------------------- #
def test_detecta_anomalias_sobre_db_local_si_existe():
    db = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
    if not os.path.exists(db):
        pytest.skip(f"BD local no encontrada: {db}")
    anoms = ai.detecta_anomalias(db)
    assert isinstance(anoms, list)
    for a in anoms:
        assert set(a.keys()) == {"slug", "nombre", "tipo", "fecha", "z", "detalle"}
        assert a["tipo"] in ("precio", "volumen")
