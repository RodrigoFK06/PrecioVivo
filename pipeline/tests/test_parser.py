"""Tests del parser de PDFs (módulo preciovivo.parser).

Solo importamos y testeamos: no modificamos parser.py.

Cubre:
  - `_num`: parseo de números (con separadores, prefijos) y faltantes (':' etc.).
  - `_split_price_trend`: separación del token pegado precio_7d+tendencia.
  - Test de integración sobre el PDF de muestra (skip si no está cacheado).
"""
from __future__ import annotations

from datetime import date

import pytest

from preciovivo.parser import _num, _split_price_trend, parse_report


# --------------------------------------------------------------------------- #
# _num : función pura
# --------------------------------------------------------------------------- #
class TestNum:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("4.18", 4.18),
            ("0.2", 0.2),
            ("64.46", 64.46),
            ("1,234.5", 1234.5),   # la coma de millares se descarta
            ("S/ 3.50", 3.50),     # prefijo de moneda se ignora
            ("  2.00  ", 2.00),    # espacios alrededor
            ("110", 110.0),
        ],
    )
    def test_parses_numbers(self, raw, expected):
        assert _num(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [":", "-", "", "   ", "—", "·"])
    def test_missing_markers_return_none(self, raw):
        # El ':' es el marcador de celda faltante más común en estos reportes.
        assert _num(raw) is None

    @pytest.mark.parametrize("raw", ["abc", ".", "Estable", None])
    def test_non_numeric_returns_none(self, raw):
        assert _num(raw) is None

    def test_returns_float_type(self):
        v = _num("12")
        assert isinstance(v, float)


# --------------------------------------------------------------------------- #
# _split_price_trend : separa el token pegado precio_7d + tendencia
# --------------------------------------------------------------------------- #
class TestSplitPriceTrend:
    """`_split_price_trend(chars)` recibe una banda de chars (no un string) y
    devuelve (precio, tendencia_normalizada, letras_crudas)."""

    def test_estable_glued(self, char_band):
        # '4.E1s8table' -> dígitos '4.18' = 4.18 ; letras 'estable' -> 'Estable'
        price, trend, raw = _split_price_trend(char_band("4.E1s8table"))
        assert price == pytest.approx(4.18)
        assert trend == "Estable"
        assert raw == "estable"

    def test_en_baja_glued(self, char_band):
        # '64E.4n6'+'Baja' = '64E.4n6Baja' -> dígitos '64.46' ; letras 'enbaja' -> 'En Baja'
        price, trend, raw = _split_price_trend(char_band("64E.4n6Baja"))
        assert price == pytest.approx(64.46)
        assert trend == "En Baja"
        assert raw == "enbaja"

    def test_en_alza_glued(self, char_band):
        price, trend, raw = _split_price_trend(char_band("1.2EnAlza"))
        assert price == pytest.approx(1.2)
        assert trend == "En Alza"

    def test_baja_notable_glued(self, char_band):
        price, trend, _ = _split_price_trend(char_band("3.0BajaNotable"))
        assert price == pytest.approx(3.0)
        assert trend == "Baja Notable"

    def test_order_is_by_x_not_list_order(self, char_band):
        # Mezclamos el orden de la lista; el split debe re-ordenar por x0.
        chars = char_band("4.18Estable")
        shuffled = list(reversed(chars))
        price, trend, _ = _split_price_trend(shuffled)
        assert price == pytest.approx(4.18)
        assert trend == "Estable"

    def test_unknown_trend_returns_none_trend_but_keeps_raw(self, char_band):
        # Tendencia desconocida -> trend None pero conservamos las letras crudas.
        price, trend, raw = _split_price_trend(char_band("5.0Zigzag"))
        assert price == pytest.approx(5.0)
        assert trend is None
        assert raw == "zigzag"


# --------------------------------------------------------------------------- #
# Integración: PDF de muestra real (skip si no está cacheado)
# --------------------------------------------------------------------------- #
class TestParseReportSample:
    def test_sample_report(self, sample_pdf):
        if sample_pdf is None:
            pytest.skip("PDF de muestra ../data/raw/reporte-335_2026-06-22.pdf no disponible")

        res = parse_report(str(sample_pdf))

        # Fecha extraída del encabezado.
        assert res.fecha == date(2026, 6, 22)

        # Conteo de productos: el reporte ronda 72 filas.
        assert 60 <= len(res.rows) <= 85, f"esperaba ~72 productos, obtuve {len(res.rows)}"

        # Cero warnings en una edición sana.
        assert res.warnings == [], f"warnings inesperados: {res.warnings}"

        # ParseResult.ok debe ser True (fecha + >=40 filas + sin warnings).
        assert res.ok is True

        # Todos los precio_hoy_kg presentes deben estar en rango sano (0.2..60 S/kg).
        precios = [r.precio_hoy_kg for r in res.rows if r.precio_hoy_kg is not None]
        assert precios, "ningún producto trae precio_hoy_kg"
        assert all(0.2 <= p <= 60 for p in precios), \
            f"precio_hoy_kg fuera de rango: {[p for p in precios if not (0.2 <= p <= 60)]}"

        # Cada fila tiene nombre no vacío y unidad asignada.
        assert all(r.producto and len(r.producto) >= 3 for r in res.rows)
        assert all(r.unidad and r.unidad != "?" for r in res.rows)
