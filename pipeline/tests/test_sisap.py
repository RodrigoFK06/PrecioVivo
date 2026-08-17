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


# --- persistencia del cross-check (offline, filas sintéticas) --------------
_CC_SINTETICO = [
    {"producto": "PAPA BLANCA", "fecha": date(2026, 7, 6), "sisap_kg": 1.50,
     "gmml_kg": 1.48, "delta_pct": 1.35, "flag": False, "detalle": "coincide (+1.4%)"},
    {"producto": "CEBOLLA ROJA", "fecha": date(2026, 7, 6), "sisap_kg": 2.10,
     "gmml_kg": 1.60, "delta_pct": 31.25, "flag": True, "detalle": "delta +31.3% > 15%"},
    {"producto": "AJO CRIOLLO", "fecha": date(2026, 7, 6), "sisap_kg": 8.00,
     "gmml_kg": None, "delta_pct": None, "flag": True,
     "detalle": "sin contraparte GMML para esa fecha en la BD"},
]


def test_build_check_summary():
    check = sisap.build_check(_CC_SINTETICO)
    assert check["fecha"] == "2026-07-06"
    assert check["contrastados"] == 3
    assert check["coinciden"] == 1
    assert check["umbral_pct"] == 15
    assert check["gmml_disponible"] is True
    assert len(check["resultados"]) == 3
    assert check["resultados"][1]["flag"] is True


def test_build_check_empty():
    check = sisap.build_check([])
    assert check["fecha"] is None
    assert check["contrastados"] == 0 and check["coinciden"] == 0
    assert check["gmml_disponible"] is False


def test_build_check_premature():
    # Sin NINGÚN gmml_kg (el 335 del día aún no se ingesta): contraste prematuro.
    prematuro = [dict(r, gmml_kg=None, delta_pct=None, flag=True,
                      detalle="sin contraparte GMML para esa fecha en la BD")
                 for r in _CC_SINTETICO]
    check = sisap.build_check(prematuro)
    assert check["gmml_disponible"] is False
    assert check["coinciden"] == 0


def _db_minima(tmp_path, filas_precio, nombre="preciovivo.db") -> str:
    """BD con el esquema REAL reducido: incluye `mercados`, que el cross-check
    necesita para no mezclar GMML con AVES/MMF2.

    `filas_precio`: [(fecha, nombre_producto, codigo_mercado, precio_hoy_kg)].
    """
    import sqlite3

    db = str(tmp_path / nombre)
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE mercados (id INTEGER PRIMARY KEY, codigo TEXT UNIQUE, nombre TEXT);
        CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre_canonico TEXT UNIQUE);
        CREATE TABLE precios_diarios (
            fecha TEXT, mercado_id INTEGER, producto_id INTEGER, precio_hoy_kg REAL);
        INSERT INTO mercados VALUES (1, 'GMML', 'Gran Mercado Mayorista de Lima');
        INSERT INTO mercados VALUES (2, 'AVES', 'Mercado de Aves');
    """)
    for fecha, producto, codigo, precio in filas_precio:
        con.execute("INSERT OR IGNORE INTO productos (nombre_canonico) VALUES (?)",
                    (producto,))
        con.execute(
            "INSERT INTO precios_diarios (fecha, mercado_id, producto_id, precio_hoy_kg) "
            "SELECT ?, m.id, p.id, ? FROM mercados m, productos p "
            "WHERE m.codigo = ? AND p.nombre_canonico = ?",
            (fecha, precio, codigo, producto))
    con.commit()
    con.close()
    return db


def test_cross_check_matches_against_sqlite(tmp_path):
    # Camino feliz: el 335 del MISMO día ya está en la BD -> se usa 'Precio Hoy'.
    db = _db_minima(tmp_path, [("2026-07-06", "Papa Blanca", "GMML", 1.48)])

    filas = [{"mercado": sisap.GMML_LABEL, "producto": "PAPA BLANCA",
              "precio_hoy_kg": 1.50, "precio_ayer_kg": 1.45, "prom_mes": None,
              "prom_7d": None, "fecha": date(2026, 7, 6)}]
    cc = sisap.cross_check(filas, db_path=db)
    assert len(cc) == 1
    c = cc[0]
    assert c["gmml_kg"] == 1.48 and c["flag"] is False
    assert abs(c["delta_pct"] - 1.35) < 0.01
    assert c["columna_sisap"] == "precio_hoy_kg"
    assert c["fecha"] == "2026-07-06"


def test_cross_check_ignora_otros_mercados(tmp_path):
    """El contraste es contra GMML, no contra cualquier mercado de la tabla.

    Sin el filtro por mercado, un producto con el mismo nombre en AVES se
    contrastaría contra el precio equivocado y el delta sería inventado.
    """
    db = _db_minima(tmp_path, [
        ("2026-07-06", "Papa Blanca", "GMML", 1.48),
        # Mismo día, mismo nombre, otro mercado y otro precio: es el señuelo.
        ("2026-07-06", "Pollo Vivo", "AVES", 7.90),
    ])
    filas = [{"mercado": sisap.GMML_LABEL, "producto": "POLLO VIVO",
              "precio_hoy_kg": 7.90, "precio_ayer_kg": 7.80, "prom_mes": None,
              "prom_7d": None, "fecha": date(2026, 7, 6)}]
    cc = sisap.cross_check(filas, db_path=db)
    assert len(cc) == 1
    # El precio de AVES NO debe usarse como contraparte GMML, aunque coincidiría.
    assert cc[0]["gmml_kg"] is None
    assert cc[0]["flag"] is True


def test_cross_check_usa_precio_ayer_cuando_no_hay_335_del_dia(tmp_path):
    """El caso REAL de todos los días: SISAP va por delante del reporte-335.

    SISAP publica ~06:30 y también publica sábados; el 335 sale después y no
    existe los fines de semana. Contrastar su 'Precio Hoy' contra nuestra BD en
    la fecha de SISAP daba cero contrapartes SIEMPRE (observado: SISAP del
    sábado 2026-08-15 contra un último 335 del viernes 14 -> 0/12 y
    `vigente=false`). SISAP trae 'Precio Ayer', que describe exactamente ese
    viernes: contra ese día sí es una comparación del mismo hecho.
    """
    db = _db_minima(tmp_path, [("2026-08-14", "Zanahoria", "GMML", 0.87)])

    filas = [{"mercado": sisap.GMML_LABEL, "producto": "ZANAHORIA",
              # 'hoy' del sábado, que nuestra serie no tiene y no debe usarse...
              "precio_hoy_kg": 0.95,
              # ...y 'ayer', que es el viernes que sí tenemos.
              "precio_ayer_kg": 0.87,
              "prom_mes": None, "prom_7d": None, "fecha": date(2026, 8, 15)}]
    cc = sisap.cross_check(filas, db_path=db)
    assert len(cc) == 1
    c = cc[0]
    assert c["columna_sisap"] == "precio_ayer_kg"
    assert c["sisap_kg"] == 0.87          # la columna 'ayer', no 0.95
    assert c["fecha"] == "2026-08-14"     # el día GMML contrastado
    assert c["fecha_sisap"] == "2026-08-15"
    assert c["gmml_kg"] == 0.87 and c["flag"] is False

    check = sisap.build_check(cc)
    assert check["gmml_disponible"] is True
    assert check["fecha"] == "2026-08-14"
    assert "Precio Ayer" in check["base"]


def test_save_and_load_check_roundtrip(tmp_path):
    db = str(tmp_path / "preciovivo.db")  # el cache vive junto a la BD
    saved = sisap.save_check(_CC_SINTETICO, db_path=db)
    loaded = sisap.load_check(db_path=db)
    assert loaded == saved
    assert loaded["fecha"] == "2026-07-06"


def test_load_check_missing_and_corrupt(tmp_path):
    db = str(tmp_path / "preciovivo.db")
    assert sisap.load_check(db_path=db) is None
    (tmp_path / "sisap_check.json").write_text("{no es json", encoding="utf-8")
    assert sisap.load_check(db_path=db) is None


# --- mapeo AVES -> ProductRow (offline, filas sintéticas) -------------------
def test_sisap_aves_rows_mapping():
    from preciovivo.ingest import _sisap_aves_rows

    rows = [
        {"mercado": sisap.AVES_LABEL, "producto": "POLLO VIVO",
         "precio_hoy_kg": 5.30, "precio_ayer_kg": 5.10, "prom_mes": 5.2,
         "prom_7d": 5.25, "fecha": date(2026, 7, 6)},
        {"mercado": sisap.GMML_LABEL, "producto": "PAPA BLANCA",
         "precio_hoy_kg": 1.50, "precio_ayer_kg": 1.48, "prom_mes": 1.5,
         "prom_7d": 1.5, "fecha": date(2026, 7, 6)},
        {"mercado": sisap.AVES_LABEL, "producto": "GALLINA",
         "precio_hoy_kg": None, "precio_ayer_kg": 6.0, "prom_mes": None,
         "prom_7d": None, "fecha": date(2026, 7, 6)},
    ]
    fecha, prs = _sisap_aves_rows(rows)
    # Solo AVES con precio_hoy: GMML se excluye (va por reporte-335) y la fila
    # sin precio no se upserta.
    assert fecha == date(2026, 7, 6)
    assert [p.producto for p in prs] == ["Pollo Vivo"]
    p = prs[0]
    # Precio ya en S/ por kg: equiv_kg=1.0 => precio_hoy_kg == precio_hoy_unit.
    assert p.equiv_kg == 1.0
    assert p.precio_hoy_kg == 5.30 and p.precio_ayer_kg == 5.10
