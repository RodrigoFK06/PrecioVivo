"""Forecasting honesto (Fase 3) para Precio Vivo.

Pronostica el precio S/ por kg de cada producto del GMML con una ESCALERA de
baselines deliberadamente simples — hay poca data (≈119 fechas hábiles), así que
nada de Prophet/GBM/redes: solo modelos que se pueden defender con honestidad.

Escalera de baselines (por producto, se elige el de menor MAE walk-forward):
  1. seasonal-naive : precio(t+h) ≈ precio del mismo día de la semana pasada
                      (5 fechas hábiles atrás); si no existe, último valor (naive).
  2. media-móvil + tendencia : nivel = media de la ventana reciente, más la
                      pendiente local (deriva lineal) extrapolada h pasos.

Métrica: validación walk-forward de VENTANA EXPANSIVA con MAE.
ANTI-LEAKAGE estricto: para cada origen t el modelo solo ve fecha<=t. Nunca
KFold, nunca shuffle, nunca un valor futuro. Es lo que hace el número honesto.

KILL-GATE de la tesis "volumen -> precio" (§6): se compara el MAE del mejor
baseline contra un modelo lineal que añade el volumen rezagado (masa_hoy lag 1)
como regresor (numpy lstsq), bajo el MISMO esquema walk-forward. Se reporta sin
maquillaje si el volumen MEJORA el MAE agregado y en qué %. Si no mejora,
volume_helps=False y se dice claramente.

Funciones puras (no escriben en la BD ni tocan otros módulos):
  - forecast_producto(series) -> dict            (contrato producto.forecast)
  - forecast_all(db_path)     -> dict            (producto.forecast + kill_gate)

La capa NO inventa números: si un producto tiene <90 observaciones de precio
no-nulas, no se pronostica (se devuelve el precio actual con metodo="sin_datos").
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

import numpy as np

# --- parámetros del módulo (constantes, no mágicos sueltos) -------------------
MIN_OBS = 90            # <90 obs de precio no-nulas -> sin pronóstico
HORIZONTE_DIAS = 1      # pronóstico a 1 fecha hábil (la próxima)
SEMANA_HABIL = 5        # "mismo día de la semana pasada" = 5 fechas hábiles atrás
VENTANA_MA = 5          # ventana de la media-móvil + tendencia
WARMUP = 30             # mínimo de obs antes de empezar a evaluar walk-forward
Z_INTERVALO = 1.0       # ancho del intervalo = ±1 desviación de los residuos


# =============================================================================
# Modelos base (funciones puras sobre un vector de precios y, índice-ordenado)
# =============================================================================

def _pred_seasonal_naive(y: np.ndarray) -> float:
    """Predice y[n] como el valor de hace SEMANA_HABIL pasos; si no, el último.

    Usa SOLO y[:n] (todo lo conocido hasta el origen). Sin look-ahead.
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


_MODELOS = {
    "seasonal_naive": _pred_seasonal_naive,
    "media_tendencia": lambda y: _pred_media_tendencia(y, HORIZONTE_DIAS),
}


# =============================================================================
# Walk-forward (ventana expansiva) — el corazón anti-leakage
# =============================================================================

def _walk_forward_mae(y: np.ndarray, pred_fn) -> tuple[float, int]:
    """MAE de un modelo bajo CV walk-forward de ventana expansiva.

    Para cada origen t (desde WARMUP hasta n-1-h) se entrena/predice usando
    EXCLUSIVAMENTE y[:t+1] y se compara contra el verdadero y[t+h]. Es ventana
    expansiva: el conjunto de entrenamiento crece, nunca encoge ni baraja.

    Devuelve (mae, n_pliegues). Si no hay pliegues suficientes -> (nan, 0).
    """
    n = len(y)
    errs = []
    for t in range(WARMUP - 1, n - HORIZONTE_DIAS):
        hist = y[: t + 1]              # SOLO pasado y presente (fecha<=t)
        yhat = pred_fn(hist)
        ytrue = y[t + HORIZONTE_DIAS]
        errs.append(abs(yhat - ytrue))
    if not errs:
        return float("nan"), 0
    return float(np.mean(errs)), len(errs)


def _walk_forward_mae_lineal(y: np.ndarray, vol: np.ndarray | None) -> tuple[float, int]:
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
    for t in range(WARMUP - 1, n - HORIZONTE_DIAS):
        # dataset de entrenamiento con features rezagadas, fecha<=t.
        # fila i (i en 1..t): target=y[i], feats=[y[i-1], (vol[i-1]), 1]
        idx = np.arange(1, t + 1)
        if len(idx) < 5:
            continue
        if vol is None:
            Xtr = np.column_stack([y[idx - 1], np.ones(len(idx))])
            x_pred = np.array([y[t], 1.0])
        else:
            Xtr = np.column_stack([y[idx - 1], vol[idx - 1], np.ones(len(idx))])
            x_pred = np.array([y[t], vol[t], 1.0])
        coef, *_ = np.linalg.lstsq(Xtr, y[idx], rcond=None)
        yhat = float(x_pred @ coef)
        errs.append(abs(yhat - y[t + HORIZONTE_DIAS]))
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
    n_obs: int            # nº de precios no-nulos


def _limpiar(series: list[dict]) -> _Serie:
    """Toma [{fecha, precio_kg, masa_hoy, ...}] ordenada y produce arrays float.

    Filtra puntos sin precio (no se puede pronosticar lo que no existe). La masa
    se conserva alineada (con NaN donde falte) para el regresor de volumen.
    """
    precios, masas = [], []
    for pt in series:
        p = pt.get("precio_kg")
        if p is None:
            continue
        precios.append(float(p))
        m = pt.get("masa_hoy")
        masas.append(float(m) if m is not None else np.nan)
    return _Serie(np.asarray(precios, float), np.asarray(masas, float), len(precios))


def _intervalo(y: np.ndarray, pred_fn, punto: float) -> tuple[float, float]:
    """Banda ±Z·sigma a partir de los residuos walk-forward in-sample.

    Honesto: el ancho refleja el error histórico real del modelo, no una fórmula
    paramétrica inventada. Si no hay residuos, banda colapsada al punto.
    """
    n = len(y)
    errs = []
    for t in range(WARMUP - 1, n - HORIZONTE_DIAS):
        errs.append(pred_fn(y[: t + 1]) - y[t + HORIZONTE_DIAS])
    if not errs:
        return (round(punto, 4), round(punto, 4))
    sigma = float(np.std(errs))
    lo = punto - Z_INTERVALO * sigma
    hi = punto + Z_INTERVALO * sigma
    return (round(max(lo, 0.0), 4), round(hi, 4))


def forecast_producto(series: list[dict]) -> dict:
    """Pronóstico honesto de un producto. Contrato: producto.forecast.

    `series`: lista ordenada por fecha de dicts con al menos {precio_kg, masa_hoy}
    (la forma de snapshot.productos[].series). Función PURA.

    Devuelve:
      {metodo, horizonte_dias, precio_estimado, intervalo:[lo,hi],
       mae_modelo, mae_baseline, n_obs}
    Productos con <MIN_OBS precios no-nulos -> metodo="sin_datos", precio actual.
    """
    s = _limpiar(series)
    y = s.precios

    # Salvaguarda de honestidad: poca data => no se pronostica.
    if s.n_obs < MIN_OBS:
        actual = float(y[-1]) if s.n_obs else 0.0
        return {
            "metodo": "sin_datos",
            "horizonte_dias": HORIZONTE_DIAS,
            "precio_estimado": round(actual, 4),
            "intervalo": [round(actual, 4), round(actual, 4)],
            "mae_modelo": None,
            "mae_baseline": None,
            "n_obs": s.n_obs,
        }

    # Elegir el mejor baseline de la escalera por MAE walk-forward.
    resultados = {}
    for nombre, fn in _MODELOS.items():
        mae, k = _walk_forward_mae(y, fn)
        if k > 0 and not np.isnan(mae):
            resultados[nombre] = (mae, fn)

    if not resultados:
        # No alcanzó para evaluar (datos al borde de WARMUP): naive honesto.
        actual = float(y[-1])
        return {
            "metodo": "naive",
            "horizonte_dias": HORIZONTE_DIAS,
            "precio_estimado": round(actual, 4),
            "intervalo": [round(actual, 4), round(actual, 4)],
            "mae_modelo": None,
            "mae_baseline": None,
            "n_obs": s.n_obs,
        }

    mejor = min(resultados, key=lambda k: resultados[k][0])
    mae_mejor, fn_mejor = resultados[mejor]
    # baseline de referencia = seasonal_naive (lo más simple que aún tiene señal)
    mae_ref = resultados.get("seasonal_naive", (mae_mejor, None))[0]

    # Pronóstico puntual usando TODA la historia disponible.
    punto = round(float(fn_mejor(y)), 4)
    intervalo = list(_intervalo(y, fn_mejor, punto))

    return {
        "metodo": mejor,
        "horizonte_dias": HORIZONTE_DIAS,
        "precio_estimado": punto,
        "intervalo": intervalo,
        "mae_modelo": round(mae_mejor, 4),
        "mae_baseline": round(mae_ref, 4),
        "n_obs": s.n_obs,
    }


# =============================================================================
# API pública global + kill-gate de volumen
# =============================================================================

def _cargar_series(db_path: str) -> dict[str, list[dict]]:
    """Lee precio_kg + masa_hoy por producto desde la BD (READ-ONLY).

    Usa psycopg si DATABASE_URL está seteado, si no sqlite3 sobre db_path.
    Devuelve {slug-no, [{fecha, precio_kg, masa_hoy}...]} ordenado por fecha.
    Clave = nombre_canonico (Integración hace el slug); se incluye en cada dict.
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
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _impute_vol(masas: np.ndarray) -> np.ndarray | None:
    """Imputa NaN de la masa con la media de los valores presentes.

    Devuelve None si hay <MIN_OBS masas presentes (no se puede testear volumen
    con honestidad para ese producto).
    """
    presentes = masas[~np.isnan(masas)]
    if len(presentes) < MIN_OBS:
        return None
    media = float(np.mean(presentes))
    out = masas.copy()
    out[np.isnan(out)] = media
    return out


def _kill_gate(series_por_prod: dict[str, list[dict]]) -> dict:
    """Contrasta la tesis volumen->precio bajo walk-forward agregado.

    El test JUSTO: para cada producto con suficiente precio Y suficiente masa,
    compara dos modelos lineales AR(1) idénticos salvo por el volumen:
       control  = precio ~ precio_lag           (mae_baseline)
       con vol  = precio ~ precio_lag + masa_lag (mae_con_volumen)
    Ambos bajo el mismo walk-forward anti-leakage. Así la diferencia se debe SOLO
    al volumen, no al precio rezagado (que es la señal dominante). Comparar contra
    un naive inflaría falsamente el aporte del volumen.

    Honesto: si el volumen no baja el MAE agregado, volume_helps=False.
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


def forecast_all(db_path: str = "../data/preciovivo.db") -> dict:
    """Pronostica todos los productos y corre el kill-gate de volumen.

    Devuelve {por_slug: {nombre_canonico -> producto.forecast}, kill_gate: {...}}.
    Función pura respecto a la BD: solo LEE. La clave de `por_slug` es el
    nombre_canonico (Integración lo mapea a slug con export.slugify).
    """
    series_por_prod = _cargar_series(db_path)
    por_slug = {nombre: forecast_producto(series)
                for nombre, series in series_por_prod.items()}
    kill_gate = _kill_gate(series_por_prod)
    return {"por_slug": por_slug, "kill_gate": kill_gate}


# =============================================================================
# __main__ — resumen honesto en consola
# =============================================================================

def _resumen(db_path: str) -> str:
    res = forecast_all(db_path)
    por = res["por_slug"]
    kg = res["kill_gate"]

    total = len(por)
    con_pron = [f for f in por.values() if f["metodo"] not in ("sin_datos",)]
    sin_pron = [f for f in por.values() if f["metodo"] == "sin_datos"]
    maes = [f["mae_modelo"] for f in con_pron if f["mae_modelo"] is not None]
    mae_medio = float(np.mean(maes)) if maes else float("nan")

    from collections import Counter
    metodos = Counter(f["metodo"] for f in por.values())

    lines = []
    lines.append("=== Precio Vivo · Forecast (Fase 3) ===")
    lines.append(f"Productos totales        : {total}")
    lines.append(f"Con pronóstico (>= {MIN_OBS} obs): {len(con_pron)}")
    lines.append(f"Sin pronóstico (poca data): {len(sin_pron)}")
    lines.append(f"MAE medio (mejor baseline): {mae_medio:.4f} S/ por kg")
    lines.append(f"Métodos elegidos          : {dict(metodos)}")
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
