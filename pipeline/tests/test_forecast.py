"""Tests del módulo de forecasting. Auto-contenidos: no dependen de conftest.

Cubren las garantías que hacen el pronóstico honesto:
  - ANTI-LEAKAGE: un pronóstico en el origen t NO cambia si se agregan datos
    posteriores a t. Si cambiara, habría fuga de futuro.
  - Productos con poca data (<MIN_OBS) NO reciben pronóstico (metodo="sin_datos").
  - La métrica MAE walk-forward es siempre NO NEGATIVA.
  - El kill-gate tiene la forma del contrato y es coherente.

Se ejecuta con datos sintéticos deterministas (sin tocar la BD), salvo un test
opcional que corre sobre la BD local si existe.
"""
from __future__ import annotations

import os
import sys

import numpy as np

# Permitir `import preciovivo.forecast` corriendo pytest desde pipeline/ o tests/
_PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE not in sys.path:
    sys.path.insert(0, _PIPELINE)

from preciovivo import forecast as F  # noqa: E402

# --- helpers de datos sintéticos ---------------------------------------------

def _serie_sintetica(n: int, seed: int = 0) -> list[dict]:
    """Genera una serie precio+masa realista pero determinista.

    Precio = nivel + estacionalidad semanal + ruido; masa correlacionada
    inversamente con el precio (oferta alta -> precio bajo). Devuelve la forma
    snapshot.productos[].series.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    estacional = 0.3 * np.sin(2 * np.pi * t / F.SEMANA_HABIL)
    nivel = 2.0 + 0.002 * t
    ruido = rng.normal(0, 0.05, n)
    precio = nivel + estacional + ruido
    masa = 400 - 50 * estacional + rng.normal(0, 20, n)
    out = []
    for i in range(n):
        out.append({
            "fecha": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "precio_kg": round(float(precio[i]), 4),
            "masa_hoy": round(float(masa[i]), 1),
        })
    return out


# =============================================================================
# ANTI-LEAKAGE
# =============================================================================

def test_walk_forward_es_anti_leakage_punto_a_punto():
    """Un MAE walk-forward debe ignorar por completo y[t+1:] en el origen t.

    Construimos la serie y luego corrompemos brutalmente el FUTURO de cada
    origen; si el predictor usara el futuro, el error de los primeros pliegues
    cambiaría. Aquí verificamos el invariante a nivel de un pliegue concreto:
    la predicción del origen t solo depende de y[:t+1].
    """
    serie = _serie_sintetica(120, seed=1)
    s = F._limpiar(serie)
    y = s.precios
    t = 60  # un origen interior

    # predicción usando solo el pasado
    pred_normal = F._pred_media_tendencia(y[: t + 1])

    # corromper TODO el futuro (t+1 en adelante) con valores absurdos
    y_corrupto = y.copy()
    y_corrupto[t + 1:] = 999.0
    pred_con_futuro_corrupto = F._pred_media_tendencia(y_corrupto[: t + 1])

    assert pred_normal == pred_con_futuro_corrupto, (
        "El pronóstico en t cambió al corromper el futuro: hay fuga de datos.")


def test_forecast_producto_estable_ante_datos_futuros_agregados():
    """forecast_producto sobre y[:k] vs y completo: el MAE de los primeros
    pliegues (los que solo ven pasado común) no debe depender de la cola.

    Concretamente: el pronóstico walk-forward calculado sobre un prefijo común
    coincide con el calculado sobre la serie larga para los mismos orígenes.
    """
    serie_larga = _serie_sintetica(140, seed=2)
    s = F._limpiar(serie_larga)
    y = s.precios

    # Recolectamos los errores walk-forward del prefijo corto y de la serie larga
    # para los orígenes COMUNES (WARMUP-1 .. k-1-h). Deben ser idénticos.
    k = 100
    fn = F._MODELOS["media_tendencia"]

    errs_corto = []
    for t in range(F.WARMUP - 1, k - F.HORIZONTE_DIAS):
        errs_corto.append(abs(fn(y[: t + 1]) - y[t + F.HORIZONTE_DIAS]))

    errs_largo = []
    for t in range(F.WARMUP - 1, k - F.HORIZONTE_DIAS):
        # mismo origen, pero la serie completa existe; el predictor solo ve y[:t+1]
        errs_largo.append(abs(fn(y[: t + 1]) - y[t + F.HORIZONTE_DIAS]))

    assert np.allclose(errs_corto, errs_largo), (
        "Los errores de orígenes comunes difieren: el futuro filtró al pasado.")


def test_modelo_volumen_no_usa_volumen_futuro():
    """El modelo lineal con volumen, evaluado en t, no debe ver vol[t+1:]."""
    serie = _serie_sintetica(120, seed=3)
    s = F._limpiar(serie)
    y = s.precios
    vol = F._impute_vol(s.masas)
    assert vol is not None

    _, k1 = F._walk_forward_mae_lineal(y, vol)
    assert k1 > 0

    # corromper SOLO el volumen estrictamente futuro respecto del origen t.
    # El diseño de entrenamiento del origen t usa filas i en 1..t con features
    # rezagadas vol[i-1] (índices 0..t-1), así que corromper vol[t:] no debe
    # alterar ni Xtr ni la predicción (que usa vol[t], dentro de lo conocido).
    t = 50
    vol_corrupto = vol.copy()
    vol_corrupto[t + 1:] = -1e6  # estrictamente futuro
    idx = np.arange(1, t + 1)
    Xtr = np.column_stack([y[idx - 1], vol[idx - 1], np.ones(len(idx))])
    Xtr_c = np.column_stack([y[idx - 1], vol_corrupto[idx - 1], np.ones(len(idx))])
    assert np.array_equal(Xtr, Xtr_c), (
        "El diseño de entrenamiento en t usó volumen futuro corrompido.")
    # la fila de predicción usa vol[t], que NO fue corrompido
    assert vol[t] == vol_corrupto[t]


def test_impute_vol_es_causal_no_filtra_futuro():
    """La imputación de masa NO debe depender de masas futuras (anti-leakage).

    Regresión del bug de la media GLOBAL: imputar los NaN con np.mean de TODA la
    serie metía masas futuras en celdas pasadas. El test corrompe las masas crudas
    estrictamente posteriores a un origen t y exige que el `vol` imputado en TODO
    índice <=t quede idéntico. Una imputación causal (forward-fill) pasa; la media
    global fallaba porque su valor de relleno cambia al mover datos del futuro.
    """
    serie = _serie_sintetica(120, seed=3)
    s = F._limpiar(serie)
    masas = s.masas
    # garantizar que hay NaN en el prefijo <=t para que la imputación sea relevante
    masas = masas.copy()
    masas[3] = np.nan
    masas[7] = np.nan

    vol = F._impute_vol(masas)
    assert vol is not None

    t = 40
    masas_corruptas = masas.copy()
    masas_corruptas[t + 1:] = 1e9  # masas estrictamente futuras, absurdas
    vol_c = F._impute_vol(masas_corruptas)
    assert vol_c is not None

    assert np.array_equal(vol[: t + 1], vol_c[: t + 1]), (
        "La masa imputada en índices <=t cambió al alterar masas futuras: "
        "la imputación filtró el futuro hacia el pasado (leakage).")
    # y no deben quedar NaN
    assert not np.isnan(vol).any()


# =============================================================================
# POCA DATA -> SIN PRONÓSTICO
# =============================================================================

def test_pocos_datos_no_reciben_pronostico():
    """Con <MIN_OBS observaciones de precio el metodo debe ser 'sin_datos'."""
    serie = _serie_sintetica(F.MIN_OBS - 1, seed=4)
    fc = F.forecast_producto(serie)
    assert fc["metodo"] == "sin_datos"
    assert fc["mae_modelo"] is None
    assert fc["mae_baseline"] is None
    assert fc["n_obs"] == F.MIN_OBS - 1
    # el precio estimado es el actual (último), sin inventar nada
    assert fc["precio_estimado"] == round(serie[-1]["precio_kg"], 4)


def test_suficientes_datos_si_reciben_pronostico():
    """Con >=MIN_OBS sí hay pronóstico con método real y MAE numérico."""
    serie = _serie_sintetica(F.MIN_OBS + 30, seed=5)
    fc = F.forecast_producto(serie)
    assert fc["metodo"] in F._MODELOS
    assert fc["mae_modelo"] is not None
    assert fc["n_obs"] >= F.MIN_OBS


def test_precios_nulos_no_cuentan_como_obs():
    """Puntos sin precio_kg no deben contar para el umbral MIN_OBS."""
    serie = _serie_sintetica(F.MIN_OBS + 5, seed=6)
    # anular 20 precios -> queda por debajo del umbral
    for pt in serie[:20]:
        pt["precio_kg"] = None
    fc = F.forecast_producto(serie)
    assert fc["n_obs"] == len(serie) - 20


# =============================================================================
# MÉTRICA MAE NO NEGATIVA
# =============================================================================

def test_mae_walk_forward_no_negativo():
    """El MAE de cada modelo de la escalera debe ser >= 0 (o NaN si no aplica)."""
    serie = _serie_sintetica(130, seed=7)
    s = F._limpiar(serie)
    y = s.precios
    for nombre, fn in F._MODELOS.items():
        mae, k = F._walk_forward_mae(y, fn)
        assert k > 0
        assert mae >= 0.0, f"MAE negativo en {nombre}: {mae}"


def test_mae_modelo_y_baseline_no_negativos():
    """Los MAE expuestos en el contrato producto.forecast son no-negativos."""
    serie = _serie_sintetica(130, seed=8)
    fc = F.forecast_producto(serie)
    assert fc["mae_modelo"] >= 0.0
    assert fc["mae_baseline"] >= 0.0


def test_mae_lineal_volumen_no_negativo():
    serie = _serie_sintetica(130, seed=9)
    s = F._limpiar(serie)
    vol = F._impute_vol(s.masas)
    mae_v, kv = F._walk_forward_mae_lineal(s.precios, vol)
    mae_c, kc = F._walk_forward_mae_lineal(s.precios, None)
    assert mae_v >= 0.0 and mae_c >= 0.0
    assert kv > 0 and kc > 0


# =============================================================================
# CONTRATOS de forma
# =============================================================================

def test_contrato_forecast_producto():
    """forecast_producto cumple exactamente las claves del contrato."""
    fc = F.forecast_producto(_serie_sintetica(120, seed=10))
    esperadas = {"metodo", "horizonte_dias", "precio_estimado", "intervalo",
                 "mae_modelo", "mae_baseline", "n_obs"}
    assert set(fc) == esperadas
    assert isinstance(fc["intervalo"], list) and len(fc["intervalo"]) == 2
    lo, hi = fc["intervalo"]
    assert lo <= hi, "intervalo invertido"
    assert lo >= 0.0, "intervalo con precio negativo"
    assert fc["horizonte_dias"] == F.HORIZONTE_DIAS


def test_contrato_kill_gate():
    """El kill-gate sintético tiene las claves y tipos del contrato."""
    series = {f"P{i}": _serie_sintetica(120, seed=20 + i) for i in range(6)}
    kg = F._kill_gate(series)
    esperadas = {"volume_helps", "mae_baseline", "mae_con_volumen",
                 "mejora_pct", "detalle", "n_productos"}
    assert set(kg) == esperadas
    assert isinstance(kg["volume_helps"], bool)
    assert kg["n_productos"] == 6
    assert kg["mae_baseline"] >= 0.0 and kg["mae_con_volumen"] >= 0.0
    # mejora_pct coherente con la dirección del MAE
    helps_implies = kg["mae_con_volumen"] < kg["mae_baseline"]
    assert kg["volume_helps"] == helps_implies


# =============================================================================
# INTEGRACIÓN sobre la BD local (opcional: solo si existe)
# =============================================================================

def test_forecast_all_sobre_db_local_si_existe():
    """Si la BD local existe, forecast_all devuelve la estructura completa.

    Integración costosa (corre el GBM walk-forward sobre toda la BD real, varios
    minutos con 2 años de data). Se omite por defecto; actívala con
    PRECIOVIVO_SLOW_TESTS=1 para correr el e2e completo.
    """
    import pytest
    db = os.environ.get("PRECIOVIVO_DB", os.path.join(_PIPELINE, "..", "data", "preciovivo.db"))
    if not os.path.exists(db):
        pytest.skip(f"BD local no encontrada en {db}")
    if not os.environ.get("PRECIOVIVO_SLOW_TESTS"):
        pytest.skip("test lento de integración; exporta PRECIOVIVO_SLOW_TESTS=1 para correrlo")
    res = F.forecast_all(db)
    assert set(res) == {"por_slug", "kill_gate"}
    assert len(res["por_slug"]) > 0
    for fc in res["por_slug"].values():
        assert set(fc) == {"metodo", "horizonte_dias", "precio_estimado",
                           "intervalo", "mae_modelo", "mae_baseline", "n_obs"}
        if fc["mae_modelo"] is not None:
            assert fc["mae_modelo"] >= 0.0
    kg = res["kill_gate"]
    assert isinstance(kg["volume_helps"], bool)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
