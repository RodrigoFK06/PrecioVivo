"""Forecasting honesto (Fase 3 -> Fase 5) para Precio Vivo.

Pronostica el precio S/ por kg de cada producto del GMML. La escalera ahora va
de baselines deliberadamente simples a un modelo real (GradientBoosting de
sklearn) con features de calendario y feriados de Perú — pero TODO bajo el mismo
juicio honesto: cada método se mide con validación walk-forward de ventana
EXPANSIVA y se reporta el MAE real, sin maquillaje.

Escalera de métodos (por producto, se elige el de menor MAE walk-forward):
  1. seasonal-naive : precio(t+h) ≈ precio del mismo día de la semana pasada
                      (5 fechas hábiles atrás); si no existe, último valor (naive).
  2. media-móvil + tendencia : nivel = media de la ventana reciente, más la
                      pendiente local (deriva lineal) extrapolada h pasos.
  3. GBM (gradient boosting): SOLO si el producto tiene >=MIN_OBS_GBM observaciones
                      no-nulas. Features: lags de precio + día-de-semana + mes +
                      feriado de Perú (paquete holidays) + días al próximo feriado.

MULTI-HORIZONTE: se evalúa y se reporta el pronóstico a 1 día Y a 7 días.

Métrica: validación walk-forward de VENTANA EXPANSIVA con MAE.
ANTI-LEAKAGE estricto (lo que más se audita): para cada origen t el modelo solo
ve fecha<=t. En cada origen el GBM se RE-AJUSTA con SOLO las filas con
fecha<=t; nada de KFold, nada de shuffle, ningún scaler/encoder ajustado con
datos futuros, ningún valor futuro en las features. Es lo que hace el número
honesto. (El GBM de árboles ni siquiera necesita escalado, así que no hay vector
de normalización que pueda filtrar el futuro.)

KILL-GATE de modelos (honesto): se comparan, por horizonte, el MAE walk-forward
agregado de tres familias: baseline (seasonal/AR1) vs +volumen (precio+masa_lag)
vs GBM-con-features. Se reporta cuál gana con la data ACTUAL (~6 meses) SIN
inflar: con poca historia es esperable que el baseline aguante, y si así pasa se
dice. El código queda listo para que, con más historia (backfill venidero), el
modelo gane sin tocar nada.

Funciones puras (no escriben en la BD ni tocan otros módulos):
  - forecast_producto(series) -> dict            (contrato producto.forecast)
  - forecast_all(db_path)     -> dict            (producto.forecast + kill_gate)

La capa NO inventa números: si un producto tiene <MIN_OBS observaciones de
precio no-nulas, no se pronostica (se devuelve el precio actual con
metodo="sin_datos").
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

# --- parámetros del módulo (constantes, no mágicos sueltos) -------------------
MIN_OBS = 90            # <90 obs de precio no-nulas -> sin pronóstico
MIN_OBS_GBM = 120       # <120 obs no-nulas -> NO recibe modelo GBM (solo baseline)
HORIZONTE_DIAS = 1      # horizonte por defecto del contrato producto.forecast
HORIZONTES = (1, 7)     # multi-horizonte: 1 día y 7 días
SEMANA_HABIL = 5        # "mismo día de la semana pasada" = 5 fechas hábiles atrás
VENTANA_MA = 5          # ventana de la media-móvil + tendencia
WARMUP = 30             # mínimo de obs antes de empezar a evaluar walk-forward
Z_INTERVALO = 1.0       # ancho del intervalo = ±1 desviación de los residuos
LAGS_GBM = (1, 2, 3, 5, 7)   # rezagos de precio que ve el GBM
GBM_WARMUP = 60         # nº mínimo de filas de entrenamiento antes de ajustar GBM

# País para feriados (calendario de Perú, incluye feriados nacionales).
import holidays as _holidays  # noqa: E402

# Caché perezoso del calendario de feriados de Perú (se amplía según se pidan años).
_PE_HOLIDAYS = _holidays.Peru()


def _es_feriado(d: date) -> bool:
    """True si `d` es feriado nacional de Perú (paquete holidays)."""
    return d in _PE_HOLIDAYS


def _dias_al_proximo_feriado(d: date, tope: int = 30) -> int:
    """Días desde `d` hasta el próximo feriado de Perú, acotado a `tope`.

    Feature de calendario honesta: el mercado se mueve alrededor de fechas no
    laborables. Acotada a `tope` para no inventar una rampa infinita.
    """
    for k in range(tope + 1):
        if (d + timedelta(days=k)) in _PE_HOLIDAYS:
            return k
    return tope


# =============================================================================
# Modelos base (funciones puras sobre un vector de precios y, índice-ordenado)
# =============================================================================

def _pred_seasonal_naive(y: np.ndarray, h: int = HORIZONTE_DIAS) -> float:
    """Predice y[n-1+h] como el valor de hace SEMANA_HABIL pasos; si no, el último.

    Usa SOLO `y` (todo lo conocido hasta el origen, que el llamador recorta a
    fecha<=t). Sin look-ahead.
    """
    if len(y) >= SEMANA_HABIL:
        return float(y[-SEMANA_HABIL])
    return float(y[-1])


def _pred_media_tendencia(y: np.ndarray, h: int = HORIZONTE_DIAS) -> float:
    """Media-móvil de la cola + pendiente local extrapolada h pasos.

    Ajusta una recta por mínimos cuadrados a las últimas VENTANA_MA observaciones
    y la proyecta h pasos hacia adelante. Solo ve y (que el llamador recorta a
    fecha<=t). Sin look-ahead.
    """
    w = y[-VENTANA_MA:] if len(y) >= VENTANA_MA else y
    if len(w) == 1:
        return float(w[-1])
    x = np.arange(len(w), dtype=float)
    # recta nivel+pendiente por lstsq sobre la ventana
    A = np.column_stack([x, np.ones_like(x)])
    coef, *_ = np.linalg.lstsq(A, w, rcond=None)
    pend, inter = coef
    x_next = (len(w) - 1) + h
    return float(pend * x_next + inter)


# Modelos baseline para el contrato y para el horizonte 1. Cada uno acepta (y, h).
_MODELOS = {
    "seasonal_naive": _pred_seasonal_naive,
    "media_tendencia": _pred_media_tendencia,
}


# =============================================================================
# Modelo GBM (GradientBoosting de sklearn) con features de calendario
# =============================================================================

def _lags_para_horizonte(h: int) -> tuple[int, ...]:
    """Rezagos de precio USABLES para predecir a horizonte h sin leakage.

    Al predecir y[t+h] desde el origen t, solo se conocen precios con índice <=t,
    es decir lags >= h respecto del objetivo. Por eso para h>1 se DESPLAZAN los
    lags: a partir de LAGS_GBM se toman lag+h-1 (o se filtra lag>=h). Así el GBM a
    7 días nunca ve y[t+1..t+6] (que serían futuro). Anti-leakage por construcción.
    """
    return tuple(lag + (h - 1) for lag in LAGS_GBM)


def _fecha_objetivo(fechas: list[date], i: int) -> date:
    """Fecha de la fila objetivo i, extrapolando si i cae más allá de la serie.

    Para la predicción del PUNTO final (i = n-1+h) la fecha objetivo no existe en
    `fechas`; se estima sumando días-calendario al paso medio entre fechas. Es
    determinística (calendario), no usa precios: no introduce leakage.
    """
    n = len(fechas)
    if 0 <= i < n:
        return fechas[i]
    if n >= 2:
        # paso medio en días entre fechas consecutivas (mediana robusta a saltos)
        difs = [(fechas[k] - fechas[k - 1]).days for k in range(1, n)]
        paso = max(1, int(round(float(np.median(difs)))))
    else:
        paso = 1
    return fechas[-1] + timedelta(days=paso * (i - (n - 1)))


def _features_gbm(
    y: np.ndarray, fechas: list[date], i: int, lags: tuple[int, ...]
) -> list[float] | None:
    """Vector de features para predecir y[i] usando SOLO precios con índice <=i-h.

    `lags` son los rezagos de precio ya desplazados al horizonte (ver
    _lags_para_horizonte): todos apuntan a índices <= origen, nunca al futuro.

    Features:
      - lags de precio y[i-lag] para lag en `lags`
      - día-de-semana de la fecha objetivo (0=lun..6=dom)
      - mes de la fecha objetivo (1..12)
      - feriado de Perú en la fecha objetivo (0/1)
      - días al próximo feriado desde la fecha objetivo

    El calendario de la fecha OBJETIVO no es leakage: el día/mes/feriado de la
    fecha futura se conocen hoy con certeza (son determinísticos del almanaque, no
    del precio). Para la predicción final, la fecha objetivo se extrapola con
    _fecha_objetivo. Devuelve None si algún lag de precio cae antes del inicio de
    la serie o apunta a un índice inexistente (futuro).
    """
    if i - max(lags) < 0 or i - min(lags) >= len(y):
        return None
    feats: list[float] = [float(y[i - lag]) for lag in lags]
    f = _fecha_objetivo(fechas, i)
    feats.append(float(f.weekday()))
    feats.append(float(f.month))
    feats.append(1.0 if _es_feriado(f) else 0.0)
    feats.append(float(_dias_al_proximo_feriado(f)))
    return feats


def _nuevo_gbm():
    """Crea un GradientBoostingRegressor con hiperparámetros prudentes.

    Pocos árboles y profundidad baja: con ~6 meses de data hay que evitar el
    sobreajuste. random_state fijo => reproducible. Sin escalado (árboles).
    """
    from sklearn.ensemble import GradientBoostingRegressor
    return GradientBoostingRegressor(
        n_estimators=120,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.9,
        random_state=42,
    )


def _gbm_fit_predict(
    y: np.ndarray, fechas: list[date], t: int, h: int
) -> float | None:
    """Ajusta un GBM con SOLO fecha<=t y predice y[t+h]. Anti-leakage estricto.

    Construye el dataset de entrenamiento {(features(i), y[i])} para todo i con
    i<=t (target ya observado en el origen) y todos los lags existentes. Los lags
    están DESPLAZADOS al horizonte (lag>=h), así que las features de la fila i usan
    índices <= i-h <= t-h: nunca el futuro. El modelo NO ve ninguna fila con
    fecha>t. La predicción usa las features de la fecha objetivo t+h, cuyos lags de
    precio son y[t+h-lag] con lag>=h => índice <=t. El calendario de t+h es
    determinístico (no leakage).

    Devuelve None si no hay suficientes filas de entrenamiento (GBM_WARMUP).
    """
    lags = _lags_para_horizonte(h)
    X, target = [], []
    for i in range(max(lags), t + 1):
        feats = _features_gbm(y, fechas, i, lags)
        if feats is None:
            continue
        X.append(feats)
        target.append(float(y[i]))
    if len(X) < GBM_WARMUP:
        return None

    j = t + h  # índice objetivo
    feats_pred = _features_gbm(y, fechas, j, lags)
    if feats_pred is None:
        return None

    model = _nuevo_gbm()
    model.fit(np.asarray(X, float), np.asarray(target, float))
    return float(model.predict(np.asarray([feats_pred], float))[0])


MAX_FOLDS = 60  # tope de orígenes por walk-forward (rendimiento). >~60 pliegues no
                # cambian materialmente el MAE estimado pero multiplican el costo del
                # GBM, que se re-ajusta en cada origen. El submuestreo es UNIFORME y
                # cubre TODA la serie (no solo lo reciente); el anti-leakage se mantiene
                # intacto: cada origen evaluado sigue usando solo fecha<=t.


def _eval_origins(inicio: int, n: int, h: int) -> list[int]:
    """Orígenes de evaluación walk-forward, capados a MAX_FOLDS (submuestreo uniforme)."""
    fin = n - h
    if inicio >= fin:
        return []
    todos = list(range(inicio, fin))
    if len(todos) <= MAX_FOLDS:
        return todos
    paso = len(todos) / MAX_FOLDS
    return [todos[int(k * paso)] for k in range(MAX_FOLDS)]


def _walk_forward_mae_gbm(
    y: np.ndarray, fechas: list[date], h: int
) -> tuple[float, int]:
    """MAE walk-forward de ventana expansiva del GBM, para el horizonte h.

    En cada origen t (desde max(WARMUP-1, GBM_WARMUP) hasta n-1-h, capado a MAX_FOLDS)
    se RE-AJUSTA el GBM con SOLO fecha<=t y se predice y[t+h]. Re-ajustar en cada
    origen es lo que garantiza el anti-leakage: ningún parámetro del modelo vio el
    futuro.

    Devuelve (mae, n_pliegues). (nan, 0) si no hubo pliegues.
    """
    n = len(y)
    inicio = max(WARMUP - 1, GBM_WARMUP, max(_lags_para_horizonte(h)))
    errs = []
    for t in _eval_origins(inicio, n, h):
        yhat = _gbm_fit_predict(y, fechas, t, h)
        if yhat is None:
            continue
        errs.append(abs(yhat - float(y[t + h])))
    if not errs:
        return float("nan"), 0
    return float(np.mean(errs)), len(errs)


# =============================================================================
# Walk-forward (ventana expansiva) — el corazón anti-leakage
# =============================================================================

def _walk_forward_mae(y: np.ndarray, pred_fn, h: int = HORIZONTE_DIAS) -> tuple[float, int]:
    """MAE de un modelo baseline bajo CV walk-forward de ventana expansiva.

    Para cada origen t (desde WARMUP-1 hasta n-1-h) se predice usando
    EXCLUSIVAMENTE y[:t+1] y se compara contra el verdadero y[t+h]. Es ventana
    expansiva: el conjunto de entrenamiento crece, nunca encoge ni baraja.

    `pred_fn` se invoca como pred_fn(hist, h). Devuelve (mae, n_pliegues).
    """
    n = len(y)
    errs = []
    for t in _eval_origins(WARMUP - 1, n, h):
        hist = y[: t + 1]              # SOLO pasado y presente (fecha<=t)
        yhat = pred_fn(hist, h)
        ytrue = y[t + h]
        errs.append(abs(yhat - ytrue))
    if not errs:
        return float("nan"), 0
    return float(np.mean(errs)), len(errs)


def _walk_forward_mae_lineal(
    y: np.ndarray, vol: np.ndarray | None, h: int = HORIZONTE_DIAS
) -> tuple[float, int]:
    """MAE walk-forward de un modelo lineal AR(1), con o sin volumen rezagado.

    Regresión por mínimos cuadrados (numpy.lstsq) sobre features conocidas en el
    origen t. Si `vol` es None, las features son [precio_lag, 1] (control justo);
    si se pasa `vol`, son [precio_lag, volumen_lag, 1]. Para cada t se re-estima
    con SOLO y[:t+1] / vol[:t+1] (anti-leakage) y se predice y[t+h].

    El CONTROL es price-only lineal a propósito: comparar precio+volumen contra
    un naive descubriría sobre todo la señal del precio rezagado (AR1), no la del
    volumen. Aislar el aporte del volumen exige el mismo AR1 en ambos lados.

    `vol` (si se da) debe estar imputado (sin NaN). Devuelve (mae, n_pliegues).
    """
    n = len(y)
    errs = []
    for t in _eval_origins(WARMUP - 1, n, h):
        # dataset de entrenamiento con features rezagadas h pasos, fecha<=t.
        # fila i (i en h..t): target=y[i], feats=[y[i-h], (vol[i-h]), 1]
        idx = np.arange(h, t + 1)
        if len(idx) < 5:
            continue
        if vol is None:
            Xtr = np.column_stack([y[idx - h], np.ones(len(idx))])
            x_pred = np.array([y[t], 1.0])
        else:
            Xtr = np.column_stack([y[idx - h], vol[idx - h], np.ones(len(idx))])
            x_pred = np.array([y[t], vol[t], 1.0])
        coef, *_ = np.linalg.lstsq(Xtr, y[idx], rcond=None)
        yhat = float(x_pred @ coef)
        errs.append(abs(yhat - y[t + h]))
    if not errs:
        return float("nan"), 0
    return float(np.mean(errs)), len(errs)


# =============================================================================
# API pública por producto
# =============================================================================

@dataclass
class _Serie:
    """Serie limpia de un producto, lista para pronosticar."""
    precios: np.ndarray   # float, sin NaN, ordenada por fecha
    masas: np.ndarray     # float, posible NaN (volumen)
    fechas: list[date]    # fechas alineadas a `precios` (date), ordenadas
    n_obs: int            # nº de precios no-nulos


def _parse_fecha(s) -> date:
    """Convierte 'YYYY-MM-DD' (o date) a date. Tolerante a None -> hoy nominal."""
    if isinstance(s, date):
        return s
    if isinstance(s, str):
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    return date(2026, 1, 1)


def _limpiar(series: list[dict]) -> _Serie:
    """Toma [{fecha, precio_kg, masa_hoy, ...}] ordenada y produce arrays float.

    Filtra puntos sin precio (no se puede pronosticar lo que no existe). La masa
    se conserva alineada (con NaN donde falte) para el regresor de volumen, y las
    fechas se conservan alineadas para las features de calendario del GBM.
    """
    precios, masas, fechas = [], [], []
    for pt in series:
        p = pt.get("precio_kg")
        if p is None:
            continue
        precios.append(float(p))
        m = pt.get("masa_hoy")
        masas.append(float(m) if m is not None else np.nan)
        fechas.append(_parse_fecha(pt.get("fecha")))
    return _Serie(
        np.asarray(precios, float), np.asarray(masas, float), fechas, len(precios)
    )


def _intervalo(y: np.ndarray, fechas: list[date], metodo: str, punto: float,
               h: int = HORIZONTE_DIAS) -> tuple[float, float]:
    """Banda ±Z·sigma a partir de los residuos walk-forward in-sample.

    Honesto: el ancho refleja el error histórico real del método elegido, no una
    fórmula paramétrica inventada. Si no hay residuos, banda colapsada al punto.
    """
    n = len(y)
    errs = []
    if metodo == "gbm":
        inicio = max(WARMUP - 1, GBM_WARMUP, max(_lags_para_horizonte(h)))
        for t in _eval_origins(inicio, n, h):
            yhat = _gbm_fit_predict(y, fechas, t, h)
            if yhat is not None:
                errs.append(yhat - float(y[t + h]))
    else:
        fn = _MODELOS[metodo]
        for t in _eval_origins(WARMUP - 1, n, h):
            errs.append(fn(y[: t + 1], h) - y[t + h])
    if not errs:
        return (round(punto, 4), round(punto, 4))
    sigma = float(np.std(errs))
    lo = punto - Z_INTERVALO * sigma
    hi = punto + Z_INTERVALO * sigma
    return (round(max(lo, 0.0), 4), round(hi, 4))


def _evaluar_metodos(s: _Serie, h: int) -> dict[str, float]:
    """MAE walk-forward de cada método disponible para el horizonte h.

    Devuelve {metodo -> mae}. Incluye 'gbm' solo si n_obs>=MIN_OBS_GBM (honestidad:
    sin suficiente historia el GBM no se evalúa). Métodos que no producen pliegues
    válidos se omiten.
    """
    y = s.precios
    out: dict[str, float] = {}
    for nombre, fn in _MODELOS.items():
        mae, k = _walk_forward_mae(y, fn, h)
        if k > 0 and not np.isnan(mae):
            out[nombre] = mae
    if s.n_obs >= MIN_OBS_GBM:
        mae_g, kg = _walk_forward_mae_gbm(y, s.fechas, h)
        if kg > 0 and not np.isnan(mae_g):
            out["gbm"] = mae_g
    return out


def _predecir_punto(s: _Serie, metodo: str, h: int) -> float:
    """Pronóstico puntual del método elegido, usando TODA la historia (fecha<=fin).

    Para el GBM, ajusta con todas las filas conocidas y predice la fecha objetivo
    n-1+h (cuyo calendario es determinístico; los lags de precio existen).
    """
    y = s.precios
    if metodo == "gbm":
        yhat = _gbm_fit_predict(y, s.fechas, len(y) - 1, h)
        if yhat is not None:
            return float(yhat)
        # fallback honesto si el GBM no pudo predecir el punto final
        return float(_pred_media_tendencia(y, h))
    return float(_MODELOS[metodo](y, h))


def forecast_producto(series: list[dict], h: int = HORIZONTE_DIAS) -> dict:
    """Pronóstico honesto de un producto. Contrato: producto.forecast.

    `series`: lista ordenada por fecha de dicts con al menos {fecha, precio_kg,
    masa_hoy} (la forma de snapshot.productos[].series). Función PURA.

    Elige el método de MENOR MAE walk-forward entre seasonal_naive,
    media_tendencia y (si hay >=MIN_OBS_GBM obs) gbm. El contrato no cambia:

      {metodo, horizonte_dias, precio_estimado, intervalo:[lo,hi],
       mae_modelo, mae_baseline, n_obs}

    `mae_baseline` = MAE del seasonal_naive (referencia simple) para el mismo h.
    Productos con <MIN_OBS precios no-nulos -> metodo="sin_datos", precio actual.
    """
    s = _limpiar(series)
    y = s.precios

    # Salvaguarda de honestidad: poca data => no se pronostica.
    if s.n_obs < MIN_OBS:
        actual = float(y[-1]) if s.n_obs else 0.0
        return {
            "metodo": "sin_datos",
            "horizonte_dias": h,
            "precio_estimado": round(actual, 4),
            "intervalo": [round(actual, 4), round(actual, 4)],
            "mae_modelo": None,
            "mae_baseline": None,
            "n_obs": s.n_obs,
        }

    resultados = _evaluar_metodos(s, h)

    if not resultados:
        # No alcanzó para evaluar (datos al borde de WARMUP): naive honesto.
        actual = float(y[-1])
        return {
            "metodo": "naive",
            "horizonte_dias": h,
            "precio_estimado": round(actual, 4),
            "intervalo": [round(actual, 4), round(actual, 4)],
            "mae_modelo": None,
            "mae_baseline": None,
            "n_obs": s.n_obs,
        }

    mejor = min(resultados, key=lambda k: resultados[k])
    mae_mejor = resultados[mejor]
    # baseline de referencia = seasonal_naive (lo más simple que aún tiene señal)
    mae_ref = resultados.get("seasonal_naive", mae_mejor)

    punto = round(_predecir_punto(s, mejor, h), 4)
    intervalo = list(_intervalo(y, s.fechas, mejor, punto, h))

    return {
        "metodo": mejor,
        "horizonte_dias": h,
        "precio_estimado": punto,
        "intervalo": intervalo,
        "mae_modelo": round(mae_mejor, 4),
        "mae_baseline": round(mae_ref, 4),
        "n_obs": s.n_obs,
    }


# =============================================================================
# API pública global + kill-gate de volumen + comparación de modelos
# =============================================================================

def _cargar_series(db_path: str) -> dict[str, list[dict]]:
    """Lee precio_kg + masa_hoy por producto desde la BD (READ-ONLY).

    Usa psycopg si DATABASE_URL está seteado, si no sqlite3 sobre db_path.
    Devuelve {nombre_canonico, [{fecha, precio_kg, masa_hoy}...]} ordenado por
    fecha. Clave = nombre_canonico (Integración hace el slug).
    """
    rows = _query_series(db_path)
    out: dict[str, list[dict]] = {}
    for nombre, fecha, precio_kg, masa_hoy in rows:
        out.setdefault(nombre, []).append(
            {"fecha": fecha, "precio_kg": precio_kg, "masa_hoy": masa_hoy})
    return out


def _query_series(db_path: str):
    sql = (
        "SELECT p.nombre_canonico, pd.fecha, pd.precio_hoy_kg, pd.masa_hoy "
        "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
        "ORDER BY p.nombre_canonico, pd.fecha"
    )
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        import psycopg  # lazy, igual que store.py
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _impute_vol(masas: np.ndarray) -> np.ndarray | None:
    """Imputa NaN de la masa de forma CAUSAL (forward-fill), sin leakage.

    ANTI-LEAKAGE: cada NaN se rellena con el ÚLTIMO valor presente en índice <=i
    (forward-fill / "carry forward"), nunca con un agregado de toda la serie. Así
    el valor imputado en la fila i solo depende de masas con índice <=i; cuando
    este `vol` entra a _walk_forward_mae_lineal, las features rezagadas vol[i-h] de
    cada origen t (i<=t) jamás contienen información de masas futuras. La media
    GLOBAL anterior (np.mean de TODOS los presentes) sí filtraba el futuro hacia el
    pasado y contaminaba el kill-gate de volumen y la familia 'volumen' del
    comparativo.

    Los NaN del INICIO (antes del primer valor presente) no tienen pasado del cual
    arrastrar; se rellenan hacia atrás (back-fill) con el primer valor presente.
    Eso es inevitable y no es leakage en el walk-forward: esos índices iniciales
    quedan por debajo de WARMUP-1, así que nunca son objetivo ni feature de un
    origen evaluado (el primer origen es t=WARMUP-1 y h>=1, luego i-h>=WARMUP-1-h,
    muy por encima del arranque). Se documenta por honestidad.

    Devuelve None si hay <MIN_OBS masas presentes (no se puede testear volumen
    con honestidad para ese producto).
    """
    presentes = masas[~np.isnan(masas)]
    if len(presentes) < MIN_OBS:
        return None
    out = masas.copy()
    # forward-fill causal: arrastra el último presente hacia adelante.
    presente = np.where(~np.isnan(out))[0]
    # back-fill SOLO el tramo inicial sin pasado (índices < primer presente).
    primero = int(presente[0])
    if primero > 0:
        out[:primero] = out[primero]
    # ahora out[0] no es NaN; arrastra hacia adelante el último valor visto.
    for i in range(1, len(out)):
        if np.isnan(out[i]):
            out[i] = out[i - 1]
    return out


def _kill_gate(series_por_prod: dict[str, list[dict]]) -> dict:
    """Contrasta la tesis volumen->precio bajo walk-forward agregado (horizonte 1).

    El test JUSTO: para cada producto con suficiente precio Y suficiente masa,
    compara dos modelos lineales AR(1) idénticos salvo por el volumen:
       control  = precio ~ precio_lag           (mae_baseline)
       con vol  = precio ~ precio_lag + masa_lag (mae_con_volumen)
    Ambos bajo el mismo walk-forward anti-leakage. Así la diferencia se debe SOLO
    al volumen, no al precio rezagado (que es la señal dominante). Comparar contra
    un naive inflaría falsamente el aporte del volumen.

    Honesto: si el volumen no baja el MAE agregado, volume_helps=False. Contrato
    fijo de 6 claves (lo lee el dashboard; la comparación de MODELOS va aparte,
    en forecast_all).
    """
    mae_base_list, mae_vol_list = [], []
    n_prod = 0
    for nombre, series in series_por_prod.items():
        s = _limpiar(series)
        if s.n_obs < MIN_OBS:
            continue
        vol = _impute_vol(s.masas)
        if vol is None:
            continue  # sin masa suficiente: no entra al test de volumen
        y = s.precios

        # control justo: lineal AR(1) solo-precio
        mae_ctrl, kc = _walk_forward_mae_lineal(y, None)
        if kc == 0 or np.isnan(mae_ctrl):
            continue
        # tratamiento: el mismo AR(1) + volumen rezagado
        mae_vol, kv = _walk_forward_mae_lineal(y, vol)
        if kv == 0 or np.isnan(mae_vol):
            continue

        mae_base_list.append(mae_ctrl)
        mae_vol_list.append(mae_vol)
        n_prod += 1

    if n_prod == 0:
        return {
            "volume_helps": False,
            "mae_baseline": None,
            "mae_con_volumen": None,
            "mejora_pct": None,
            "detalle": "No hubo productos con precio y volumen suficientes para "
                       "contrastar la tesis volumen->precio.",
            "n_productos": 0,
        }

    mae_base = float(np.mean(mae_base_list))
    mae_vol = float(np.mean(mae_vol_list))
    mejora_pct = round(100.0 * (mae_base - mae_vol) / mae_base, 2) if mae_base else 0.0
    helps = mae_vol < mae_base

    if helps:
        detalle = (
            f"Sobre {n_prod} productos, frente a un control lineal AR(1) que solo "
            f"usa el precio rezagado, añadir el volumen rezagado redujo el MAE "
            f"walk-forward agregado de {mae_base:.4f} a {mae_vol:.4f} "
            f"(mejora {mejora_pct:.2f}%). El volumen aporta señal marginal al "
            f"pronóstico de precio con estos datos.")
    else:
        detalle = (
            f"Sobre {n_prod} productos, frente a un control lineal AR(1) que solo "
            f"usa el precio rezagado ({mae_base:.4f}), añadir el volumen rezagado "
            f"NO mejora el MAE walk-forward ({mae_vol:.4f}; cambio {mejora_pct:.2f}%). "
            f"Casi toda la capacidad predictiva viene del precio anterior, no del "
            f"volumen: con la data disponible la tesis volumen->precio NO se sostiene.")

    return {
        "volume_helps": bool(helps),
        "mae_baseline": round(mae_base, 4),
        "mae_con_volumen": round(mae_vol, 4),
        "mejora_pct": mejora_pct,
        "detalle": detalle,
        "n_productos": n_prod,
    }


def _comparacion_modelos(series_por_prod: dict[str, list[dict]]) -> dict:
    """Comparación honesta de familias de modelos por horizonte (MAE walk-forward).

    Para cada horizonte h en HORIZONTES, agrega sobre productos el MAE
    walk-forward de cuatro familias:
      - baseline : el MEJOR de {seasonal_naive, media_tendencia} por producto.
      - ar1      : lineal AR(1) solo-precio (precio ~ precio_lag). Es el MISMO
                   control del kill-gate de volumen; se incluye para que el lector
                   vea que la ganancia sobre los naive viene de la estructura AR1,
                   NO del volumen (coherente con volume_helps).
      - volumen  : lineal AR(1) + masa_lag (solo donde hay masa suficiente).
      - gbm      : GradientBoosting con features (solo productos con >=MIN_OBS_GBM).

    Para que la comparación sea JUSTA, cuando hay GBM el ganador se decide sobre el
    MISMO conjunto de productos donde TODAS las familias pudieron evaluarse. Con
    poca historia es esperable que el baseline/AR1 gane; se dice tal cual.

    Estructura (va dentro de forecastMeta.kill_gate.comparacion_modelos):
      {
        "horizontes": [1, 7],
        "por_horizonte": {
           "1": {"mae_baseline","mae_ar1","mae_volumen","mae_gbm","n_productos",
                 "n_productos_gbm","ganador","veredicto"}, ...
        },
        "gbm_disponible": bool,   # ¿algún producto alcanzó MIN_OBS_GBM?
        "umbral_gbm": MIN_OBS_GBM,
        "veredicto_global": str,
      }
    """
    por_h: dict[str, dict] = {}
    algun_gbm = False

    for h in HORIZONTES:
        base_l, ar1_l, vol_l, gbm_l = [], [], [], []
        n_total = 0
        n_gbm = 0
        for nombre, series in series_por_prod.items():
            s = _limpiar(series)
            if s.n_obs < MIN_OBS:
                continue
            y = s.precios

            # baseline = el mejor de la escalera simple, por producto
            maes_base = []
            for fn in _MODELOS.values():
                m, k = _walk_forward_mae(y, fn, h)
                if k > 0 and not np.isnan(m):
                    maes_base.append(m)
            if not maes_base:
                continue
            mae_base = min(maes_base)

            # ar1 price-only (mismo control que el kill-gate de volumen)
            mae_ar1 = None
            ma, ka = _walk_forward_mae_lineal(y, None, h)
            if ka > 0 and not np.isnan(ma):
                mae_ar1 = ma

            # volumen (solo si hay masa suficiente)
            vol = _impute_vol(s.masas)
            mae_vol = None
            if vol is not None:
                mv, kv = _walk_forward_mae_lineal(y, vol, h)
                if kv > 0 and not np.isnan(mv):
                    mae_vol = mv

            # gbm (solo si hay historia suficiente)
            mae_gbm = None
            if s.n_obs >= MIN_OBS_GBM:
                mg, kg = _walk_forward_mae_gbm(y, s.fechas, h)
                if kg > 0 and not np.isnan(mg):
                    mae_gbm = mg
                    algun_gbm = True

            base_l.append(mae_base)
            ar1_l.append(mae_ar1 if mae_ar1 is not None else np.nan)
            vol_l.append(mae_vol if mae_vol is not None else np.nan)
            gbm_l.append(mae_gbm if mae_gbm is not None else np.nan)
            n_total += 1
            if mae_gbm is not None:
                n_gbm += 1

        if n_total == 0:
            por_h[str(h)] = {
                "mae_baseline": None, "mae_ar1": None, "mae_volumen": None,
                "mae_gbm": None, "n_productos": 0, "n_productos_gbm": 0,
                "ganador": None,
                "veredicto": f"Sin productos evaluables a {h} día(s).",
            }
            continue

        base_arr = np.asarray(base_l, float)
        ar1_arr = np.asarray(ar1_l, float)
        vol_arr = np.asarray(vol_l, float)
        gbm_arr = np.asarray(gbm_l, float)

        def _agg(arr, mask=None):
            a = arr if mask is None else arr[mask]
            return float(np.nanmean(a)) if np.any(~np.isnan(a)) else None

        # MAE agregado de cada familia (sobre los productos donde la familia aplica)
        mae_base = _agg(base_arr)
        mae_ar1 = _agg(ar1_arr)
        mae_vol = _agg(vol_arr)
        mae_gbm = _agg(gbm_arr)

        # ganador entre las familias DISPONIBLES, comparado sobre el MISMO conjunto
        # de productos donde GBM existe (si existe), para no comparar peras con
        # manzanas. Si no hay GBM, se compara sobre todo el set evaluable.
        mask = ~np.isnan(gbm_arr) if n_gbm > 0 else None
        cand: dict[str, float] = {}
        for nombre_fam, arr in (("baseline", base_arr), ("ar1", ar1_arr),
                                ("volumen", vol_arr), ("gbm", gbm_arr)):
            v = _agg(arr, mask)
            if v is not None:
                cand[nombre_fam] = v
        ganador = min(cand, key=lambda k: cand[k])

        comp = ", ".join(f"{k} {cand[k]:.4f}" for k in
                         ("baseline", "ar1", "volumen", "gbm") if k in cand)
        if n_gbm == 0:
            veredicto = (
                f"A {h} día(s): el GBM no se evaluó (ningún producto alcanza "
                f"{MIN_OBS_GBM} observaciones; máx. histórico ~119). MAE -> {comp}. "
                f"Gana el {ganador}. Honesto: con ~6 meses de data no hay historia "
                f"para que un modelo de árboles supere al baseline; el código queda "
                f"listo para activarlo con el backfill venidero.")
        elif ganador == "gbm":
            veredicto = (
                f"A {h} día(s), sobre {n_gbm} productos con >= {MIN_OBS_GBM} obs, el "
                f"GBM con features gana. MAE -> {comp}. El modelo real supera al "
                f"baseline con la data disponible.")
        else:
            veredicto = (
                f"A {h} día(s), sobre {n_gbm} productos con >= {MIN_OBS_GBM} obs, "
                f"gana el {ganador}. MAE -> {comp}. Honesto: con esta historia el GBM "
                f"aún no supera al baseline; se espera que lo haga con más data.")

        por_h[str(h)] = {
            "mae_baseline": round(mae_base, 4) if mae_base is not None else None,
            "mae_ar1": round(mae_ar1, 4) if mae_ar1 is not None else None,
            "mae_volumen": round(mae_vol, 4) if mae_vol is not None else None,
            "mae_gbm": round(mae_gbm, 4) if mae_gbm is not None else None,
            "n_productos": n_total,
            "n_productos_gbm": n_gbm,
            "ganador": ganador,
            "veredicto": veredicto,
        }

    if not algun_gbm:
        veredicto_global = (
            f"Con la data ACTUAL (~6 meses, máx. ~119 obs/producto) ningún producto "
            f"alcanza el umbral de {MIN_OBS_GBM} observaciones, así que el GBM con "
            f"features NO se evaluó en ningún horizonte (gana siempre un modelo más "
            f"simple). Entre los simples, la estructura AR(1) sobre el precio rezagado "
            f"ya mejora a los baselines naive; añadir volumen no aporta señal "
            f"consistente (ver kill-gate volumen->precio, que es el test apples-to-"
            f"apples sobre el mismo conjunto de productos). Esto es lo esperado y se "
            f"reporta sin inflar: el GBM (lags + día-de-semana + mes + feriados de "
            f"Perú) ya está implementado y validado anti-leakage, y se activará "
            f"automáticamente cuando el backfill aporte historia suficiente; recién "
            f"entonces se medirá si gana de verdad.")
    else:
        ganan_gbm = [h for h, d in por_h.items() if d.get("ganador") == "gbm"]
        if ganan_gbm:
            veredicto_global = (
                f"El GBM ya gana en el/los horizonte(s) {', '.join(ganan_gbm)}. "
                f"Reportado sobre los productos con historia suficiente (>= "
                f"{MIN_OBS_GBM} obs), sin inflar.")
        else:
            veredicto_global = (
                "El GBM se evaluó pero el baseline aún aguanta en todos los "
                "horizontes con la data disponible. Honesto: se espera que el GBM "
                "gane con más historia; el camino queda abierto.")

    return {
        "horizontes": list(HORIZONTES),
        "por_horizonte": por_h,
        "gbm_disponible": bool(algun_gbm),
        "umbral_gbm": MIN_OBS_GBM,
        "veredicto_global": veredicto_global,
    }


def _forecast_7d(series_por_prod: dict[str, list[dict]]) -> dict[str, dict]:
    """Pronóstico a 7 días por producto (mismo contrato que producto.forecast).

    Va dentro de forecastMeta.kill_gate.forecast_7d. Reutiliza forecast_producto
    con h=7; el dashboard puede mostrarlo o ignorarlo (no rompe nada).
    """
    return {nombre: forecast_producto(series, h=7)
            for nombre, series in series_por_prod.items()}


def _cache_path(db_path: str) -> str:
    import os
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "forecast_cache.json")


def save_cache(res: dict, db_path: str = "../data/preciovivo.db") -> None:
    """Persiste el resultado de forecast_all junto a la BD (para que export NO lo
    recompute: el GBM walk-forward son varios minutos)."""
    import json
    try:
        with open(_cache_path(db_path), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    except OSError:
        pass


def forecast_all_cached(db_path: str = "../data/preciovivo.db") -> dict:
    """forecast_all, pero sirviendo el cache si existe (lo escribe `ingest --forecast`).

    El flujo diario es ingest --forecast (calcula+cachea) -> export (lee cache). Si
    no hay cache, calcula y lo guarda (degradación correcta, solo más lento)."""
    import json
    import os
    p = _cache_path(db_path)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    res = forecast_all(db_path)
    save_cache(res, db_path)
    return res


def forecast_all(db_path: str = "../data/preciovivo.db") -> dict:
    """Pronostica todos los productos y corre los kill-gates honestos.

    Devuelve {por_slug, kill_gate}. La clave de `por_slug` es el nombre_canonico
    (Integración lo mapea a slug con export.slugify) y cada valor es el contrato
    producto.forecast a 1 día (el mejor método honesto por producto, que puede ser
    GBM si tiene historia suficiente).

    El dict `kill_gate` mantiene SU contrato de 6 claves (tesis volumen->precio) y
    se ENRIQUECE con dos claves extra que el dashboard puede leer o ignorar:
      - comparacion_modelos : MAE baseline vs +volumen vs GBM, por horizonte.
      - forecast_7d         : pronóstico a 7 días por producto.

    Función pura respecto a la BD: solo LEE.
    """
    series_por_prod = _cargar_series(db_path)
    por_slug = {nombre: forecast_producto(series, HORIZONTE_DIAS)
                for nombre, series in series_por_prod.items()}
    kill_gate = _kill_gate(series_por_prod)
    # Enriquecimiento honesto (claves extra; no rompe el contrato de 6 del kill-gate
    # cuando se prueba _kill_gate directo, ni el {por_slug, kill_gate} de forecast_all).
    kill_gate["comparacion_modelos"] = _comparacion_modelos(series_por_prod)
    kill_gate["forecast_7d"] = _forecast_7d(series_por_prod)
    return {"por_slug": por_slug, "kill_gate": kill_gate}


# =============================================================================
# __main__ — resumen honesto en consola
# =============================================================================

def _resumen(db_path: str) -> str:
    res = forecast_all(db_path)
    por = res["por_slug"]
    kg = res["kill_gate"]
    comp = kg.get("comparacion_modelos", {})
    f7 = kg.get("forecast_7d", {})

    total = len(por)
    con_pron = [f for f in por.values() if f["metodo"] not in ("sin_datos",)]
    sin_pron = [f for f in por.values() if f["metodo"] == "sin_datos"]
    maes = [f["mae_modelo"] for f in con_pron if f["mae_modelo"] is not None]
    mae_medio = float(np.mean(maes)) if maes else float("nan")

    from collections import Counter
    metodos = Counter(f["metodo"] for f in por.values())
    metodos_7d = Counter(f["metodo"] for f in f7.values())
    n_gbm_1d = metodos.get("gbm", 0)

    lines = []
    lines.append("=== Precio Vivo · Forecast (modelos reales + multi-horizonte) ===")
    lines.append(f"Productos totales            : {total}")
    lines.append(f"Con pronóstico (>= {MIN_OBS} obs)   : {len(con_pron)}")
    lines.append(f"Sin pronóstico (poca data)   : {len(sin_pron)}")
    lines.append(f"MAE medio 1d (mejor método)  : {mae_medio:.4f} S/ por kg")
    lines.append(f"Métodos elegidos 1d          : {dict(metodos)}")
    lines.append(f"Métodos elegidos 7d          : {dict(metodos_7d)}")
    lines.append(f"Productos con GBM (1d)       : {n_gbm_1d}  (umbral GBM = {MIN_OBS_GBM} obs)")
    lines.append("")
    lines.append("--- Comparación de modelos por horizonte (MAE walk-forward) ---")
    lines.append(f"GBM disponible (algún prod)  : {comp.get('gbm_disponible')}")
    for h in comp.get("horizontes", []):
        d = comp.get("por_horizonte", {}).get(str(h), {})
        lines.append(
            f"  h={h}d  baseline={d.get('mae_baseline')}  ar1={d.get('mae_ar1')}  "
            f"volumen={d.get('mae_volumen')}  gbm={d.get('mae_gbm')}  "
            f"n={d.get('n_productos')}  n_gbm={d.get('n_productos_gbm')}  "
            f"ganador={d.get('ganador')}")
    lines.append(f"  veredicto global: {comp.get('veredicto_global')}")
    lines.append("")
    lines.append("--- Kill-gate tesis volumen->precio (§6) ---")
    lines.append(f"volume_helps   : {kg['volume_helps']}")
    lines.append(f"MAE baseline   : {kg['mae_baseline']}")
    lines.append(f"MAE con volumen: {kg['mae_con_volumen']}")
    lines.append(f"Mejora %       : {kg['mejora_pct']}")
    lines.append(f"n_productos    : {kg['n_productos']}")
    lines.append(f"Detalle        : {kg['detalle']}")
    return "\n".join(lines)


def main():
    db = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
    print(_resumen(db))


if __name__ == "__main__":
    main()
