"""Pruebas del motor de alertas (preciovivo.alerts).

Auto-contenidas: NO requieren clave de API, red ni la BD local. Verifican que:
  - una regla de umbral var_pct dispara cuando se cruza y NO dispara cuando no;
  - una regla de nivel (precio cruza un valor absoluto) dispara/no-dispara bien;
  - la watchlist restringe a los productos vigilados;
  - la regla de anomalía REUTILIZA ai.detecta_anomalias (sin reimplementar z);
  - la 'carga lista para emisor' tiene la forma esperada y no realiza envíos.
"""
from __future__ import annotations

import pytest

from preciovivo.alerts import (
    DEFAULT_REGLAS,
    AlertaDisparada,
    Regla,
    carga_para_emisor,
    evaluate,
)


# --------------------------------------------------------------------------- #
# Snapshots sintéticos (la forma de export.build: productos[].latest/series)
# --------------------------------------------------------------------------- #
def _producto(nombre, slug, precio_kg, var_pct, fecha="2026-06-23", masa=100.0):
    return {
        "nombre": nombre,
        "slug": slug,
        "categoria": "Hortalizas",
        "unidad": "Kilogramo",
        "latest": {
            "fecha": fecha,
            "precio_kg": precio_kg,
            "precio_ayer_kg": (precio_kg / (1 + var_pct / 100.0)
                               if var_pct is not None else None),
            "var_pct": var_pct,
            "masa_hoy": masa,
            "tendencia": "Estable",
        },
        "series": [],
    }


def _snapshot(productos, fecha="2026-06-23"):
    return {
        "mercado": "Gran Mercado Mayorista de Lima (GMML)",
        "latestFecha": fecha,
        "productCount": len(productos),
        "productos": productos,
    }


# --------------------------------------------------------------------------- #
# Regla var_pct: dispara / no-dispara
# --------------------------------------------------------------------------- #
def test_var_pct_dispara_sobre_umbral():
    snap = _snapshot([
        _producto("Brocoli", "brocoli", 3.50, var_pct=40.0),     # cruza +15%
        _producto("Papa", "papa", 1.20, var_pct=2.0),            # NO cruza
    ])
    regla = Regla(tipo="var_pct", umbral=15.0, direccion="ambas",
                  nombre="test-var")
    alertas = evaluate(snap, [regla])
    nombres = {a.producto for a in alertas}
    assert "Brocoli" in nombres
    assert "Papa" not in nombres
    a = next(x for x in alertas if x.producto == "Brocoli")
    assert isinstance(a, AlertaDisparada)
    assert a.tipo == "var_pct"
    assert a.valor == pytest.approx(40.0)
    assert a.fecha == "2026-06-23"
    assert "Brocoli" in a.mensaje and "%" in a.mensaje


def test_var_pct_no_dispara_si_nadie_cruza():
    snap = _snapshot([
        _producto("Papa", "papa", 1.20, var_pct=3.0),
        _producto("Tomate", "tomate", 4.00, var_pct=-5.0),
    ])
    regla = Regla(tipo="var_pct", umbral=15.0, nombre="test-var")
    assert evaluate(snap, [regla]) == []


def test_var_pct_direccion_solo_bajas():
    snap = _snapshot([
        _producto("Brocoli", "brocoli", 3.50, var_pct=40.0),    # suba: NO debe disparar
        _producto("Tomate", "tomate", 4.00, var_pct=-20.0),     # baja: SÍ
    ])
    regla = Regla(tipo="var_pct", umbral=15.0, direccion="baja",
                  nombre="solo-bajas")
    alertas = evaluate(snap, [regla])
    nombres = {a.producto for a in alertas}
    assert nombres == {"Tomate"}


def test_var_pct_borde_igual_al_umbral_dispara():
    snap = _snapshot([_producto("Cebolla", "cebolla", 2.0, var_pct=15.0)])
    regla = Regla(tipo="var_pct", umbral=15.0, nombre="borde")
    assert len(evaluate(snap, [regla])) == 1


# --------------------------------------------------------------------------- #
# Regla nivel: precio cruza un valor absoluto
# --------------------------------------------------------------------------- #
def test_nivel_dispara_por_encima():
    snap = _snapshot([
        _producto("Fresa", "fresa", 12.0, var_pct=1.0),    # >= 10 -> dispara
        _producto("Papa", "papa", 1.5, var_pct=1.0),       # < 10 -> no
    ])
    regla = Regla(tipo="nivel", umbral=10.0, direccion="sube",
                  nombre="caro>=10")
    alertas = evaluate(snap, [regla])
    assert {a.producto for a in alertas} == {"Fresa"}
    assert alertas[0].valor == pytest.approx(12.0)


def test_nivel_dispara_por_debajo():
    snap = _snapshot([
        _producto("Papa", "papa", 0.80, var_pct=1.0),      # <= 1.0 -> dispara
        _producto("Fresa", "fresa", 12.0, var_pct=1.0),    # > 1.0 -> no
    ])
    regla = Regla(tipo="nivel", umbral=1.0, direccion="baja",
                  nombre="barato<=1")
    alertas = evaluate(snap, [regla])
    assert {a.producto for a in alertas} == {"Papa"}


# --------------------------------------------------------------------------- #
# Watchlist: restringe los productos vigilados
# --------------------------------------------------------------------------- #
def test_watchlist_restringe_a_producto_vigilado():
    snap = _snapshot([
        _producto("Brocoli", "brocoli", 3.50, var_pct=40.0),
        _producto("Beterraga", "beterraga", 4.33, var_pct=39.5),
    ])
    # Watchlist por nombre (con acento/mayúsculas distintos -> se normaliza a slug).
    regla = Regla(tipo="var_pct", umbral=15.0,
                  watchlist=frozenset({"Brócoli"}), nombre="solo-brocoli")
    alertas = evaluate(snap, [regla])
    assert {a.producto for a in alertas} == {"Brocoli"}


def test_watchlist_vacia_aplica_a_todos():
    snap = _snapshot([
        _producto("Brocoli", "brocoli", 3.50, var_pct=40.0),
        _producto("Beterraga", "beterraga", 4.33, var_pct=39.5),
    ])
    regla = Regla(tipo="var_pct", umbral=15.0, nombre="todos")
    assert len(evaluate(snap, [regla])) == 2


# --------------------------------------------------------------------------- #
# Regla anomalia: REUTILIZA ai.detecta_anomalias
# --------------------------------------------------------------------------- #
def _snapshot_con_serie_anomala():
    """Snapshot sin 'anomalias' precomputadas: fuerza el cálculo vía ai."""
    serie = (
        [{"fecha": f"2026-05-{i+1:02d}", "precio_kg": 5.0, "masa_hoy": 100.0}
         for i in range(20)]
        + [{"fecha": "2026-06-01", "precio_kg": 30.0, "masa_hoy": 100.0}]
    )
    prod = {
        "nombre": "Tomate", "slug": "tomate", "categoria": "Hortalizas",
        "unidad": "Kilogramo",
        "latest": {"fecha": "2026-06-01", "precio_kg": 30.0, "var_pct": None,
                   "masa_hoy": 100.0, "tendencia": "Estable"},
        "series": serie,
    }
    return _snapshot([prod], fecha="2026-06-01")


def test_anomalia_reutiliza_detecta_anomalias(monkeypatch):
    """La regla de anomalía debe delegar en ai.detecta_anomalias (no reimplementar)."""
    from preciovivo import ai

    snap = _snapshot_con_serie_anomala()  # sin clave 'anomalias' -> debe calcular

    # Espía: confirmamos que evaluate() llama a ai.detecta_anomalias.
    llamado = {"n": 0}
    real = ai.detecta_anomalias

    def _spy(origen, *a, **k):
        llamado["n"] += 1
        return real(origen, *a, **k)

    monkeypatch.setattr(ai, "detecta_anomalias", _spy)

    regla = Regla(tipo="anomalia", umbral=3.5, nombre="anom")
    alertas = evaluate(snap, [regla])

    assert llamado["n"] >= 1, "evaluate no reutilizó ai.detecta_anomalias"
    assert any(a.producto == "Tomate" and a.tipo == "anomalia" for a in alertas)
    a = next(x for x in alertas if x.producto == "Tomate")
    assert abs(a.valor) >= 3.5
    assert a.fecha == "2026-06-01"


def test_anomalia_usa_anomalias_precomputadas_del_snapshot():
    """Si el snapshot ya trae 'anomalias', la regla las consume directamente."""
    snap = _snapshot([_producto("Tomate", "tomate", 30.0, var_pct=None)],
                     fecha="2026-06-23")
    snap["anomalias"] = [{
        "slug": "tomate", "nombre": "Tomate", "tipo": "precio",
        "fecha": "2026-06-23", "z": 8.0, "detalle": "precio S/ 30.00/kg",
    }]
    regla = Regla(tipo="anomalia", umbral=3.5, nombre="anom")
    alertas = evaluate(snap, [regla])
    assert len(alertas) == 1
    assert alertas[0].valor == pytest.approx(8.0)


def test_anomalia_umbral_alto_filtra():
    snap = _snapshot([_producto("Tomate", "tomate", 6.0, var_pct=None)])
    snap["anomalias"] = [{
        "slug": "tomate", "nombre": "Tomate", "tipo": "precio",
        "fecha": "2026-06-23", "z": 4.0, "detalle": "precio S/ 6.00/kg",
    }]
    regla = Regla(tipo="anomalia", umbral=6.0, nombre="anom-estricta")
    assert evaluate(snap, [regla]) == []


# --------------------------------------------------------------------------- #
# evaluate: combinación, dedup y orden
# --------------------------------------------------------------------------- #
def test_evaluate_ordena_por_magnitud_descendente():
    snap = _snapshot([
        _producto("Brocoli", "brocoli", 3.50, var_pct=40.0),
        _producto("Cebolla China", "cebolla-china", 2.38, var_pct=26.6),
    ])
    regla = Regla(tipo="var_pct", umbral=15.0, nombre="var")
    alertas = evaluate(snap, [regla])
    valores = [abs(a.valor) for a in alertas]
    assert valores == sorted(valores, reverse=True)


def test_evaluate_default_reglas_no_falla_sobre_snapshot_sintetico():
    snap = _snapshot([_producto("Brocoli", "brocoli", 3.50, var_pct=40.0)])
    alertas = evaluate(snap, DEFAULT_REGLAS)
    assert isinstance(alertas, list)
    assert any(a.tipo == "var_pct" for a in alertas)


def test_regla_tipo_invalido_lanza():
    with pytest.raises(ValueError):
        Regla(tipo="inexistente", umbral=1.0)


def test_evaluate_acepta_none_reglas_usa_default():
    snap = _snapshot([_producto("Brocoli", "brocoli", 3.50, var_pct=40.0)])
    alertas = evaluate(snap, None)
    assert any(a.tipo == "var_pct" for a in alertas)


# --------------------------------------------------------------------------- #
# carga_para_emisor: forma del payload, sin enviar nada
# --------------------------------------------------------------------------- #
def test_carga_para_emisor_forma_con_alertas():
    snap = _snapshot([_producto("Brocoli", "brocoli", 3.50, var_pct=40.0)])
    alertas = evaluate(snap, [Regla(tipo="var_pct", umbral=15.0, nombre="v")])
    carga = carga_para_emisor(alertas, canal="email", snapshot=snap)

    assert carga["version"] == 1
    assert carga["canal"] == "email"
    assert carga["n_alertas"] == len(alertas) == 1
    assert carga["fecha_datos"] == "2026-06-23"
    assert carga["mercado"].startswith("Gran Mercado")
    assert isinstance(carga["asunto"], str) and carga["asunto"]
    assert isinstance(carga["alertas"], list)
    # Cada alerta es un dict serializable con el contrato.
    a0 = carga["alertas"][0]
    assert set(a0.keys()) == {
        "producto", "slug", "tipo", "mensaje", "valor", "fecha", "regla"}


def test_carga_para_emisor_sin_alertas():
    carga = carga_para_emisor([], canal="whatsapp")
    assert carga["n_alertas"] == 0
    assert carga["alertas"] == []
    assert "sin alertas" in carga["asunto"].lower()


# --------------------------------------------------------------------------- #
# evaluate acepta una ruta a la BD (str) — integración opcional con la BD local
# --------------------------------------------------------------------------- #
def test_evaluate_sobre_db_local_si_existe():
    import os

    db = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
    if not os.path.exists(db):
        pytest.skip(f"BD local no encontrada: {db}")
    if not os.environ.get("PRECIOVIVO_SLOW_TESTS"):
        # evaluate(db) reconstruye el snapshot (corre el forecast GBM, varios
        # minutos con 2 años). Costoso: se omite por defecto.
        pytest.skip("test lento de integración; exporta PRECIOVIVO_SLOW_TESTS=1")
    monkey = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        alertas = evaluate(db)  # usa DEFAULT_REGLAS
    finally:
        if monkey is not None:
            os.environ["ANTHROPIC_API_KEY"] = monkey
    assert isinstance(alertas, list)
    for a in alertas:
        assert isinstance(a, AlertaDisparada)
        assert a.tipo in ("var_pct", "nivel", "anomalia")
        assert isinstance(a.mensaje, str) and a.mensaje
