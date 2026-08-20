"""Pruebas del dead-man's-switch (`scripts/frescura.py`).

No las tenía. Es el guard que vigila el modo de falla característico de este
proyecto —dejar de actualizarse en silencio— y no estaba cubierto por nada:
si se rompiera, el sistema perdería su único aviso de que el pipeline murió,
y lo perdería en silencio. Un vigilante sin vigilancia.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))
import frescura  # noqa: E402


def _snapshot(tmp_path: Path, ultima: str, verificacion: dict | None = None) -> Path:
    datos = {"latestFecha": ultima, "productCount": 72}
    if verificacion is not None:
        datos["verificacion"] = verificacion
    ruta = tmp_path / "snapshot.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


# --------------------------------------------------------------------------- #
# Frescura del dato
# --------------------------------------------------------------------------- #
def test_el_fin_de_semana_no_dispara_la_alarma(tmp_path: Path):
    """Viernes publicado, se revisa el lunes: cero días HÁBILES de atraso.

    Medir en días naturales daría falsa alarma cada lunes, y una alarma que
    suena cuando no pasa nada deja de mirarse.
    """
    r = frescura.revisar(_snapshot(tmp_path, "2026-08-14"),  # viernes
                         hoy=date(2026, 8, 17))              # lunes
    # 1, no 3: `dias_habiles_entre` cuenta los hábiles POSTERIORES a la fecha
    # del dato, y sábado y domingo no cuentan. En días naturales serían 3 y el
    # umbral de 2 dispararía cada lunes.
    assert r["dias_habiles_de_atraso"] == 1
    assert r["ok"]


def test_una_semana_sin_publicar_si_dispara(tmp_path: Path):
    r = frescura.revisar(_snapshot(tmp_path, "2026-08-10"), hoy=date(2026, 8, 17))
    assert not r["ok"]
    assert r["estado"] == "desactualizado"


def test_un_snapshot_ilegible_es_fallo_no_excepcion(tmp_path: Path):
    ruta = tmp_path / "snapshot.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    r = frescura.revisar(ruta, hoy=date(2026, 8, 20))
    assert not r["ok"] and r["estado"] == "snapshot_ilegible"


# --------------------------------------------------------------------------- #
# Frescura del CONTRASTE — lo que se añadió tras el primer día autónomo
# --------------------------------------------------------------------------- #
def test_contraste_al_dia(tmp_path: Path):
    r = frescura.revisar(
        _snapshot(tmp_path, "2026-08-19", {"fecha": "2026-08-19", "vigente": True}),
        hoy=date(2026, 8, 20))
    assert r["verificacion_ok"]
    assert r["verificacion_dias_de_atraso"] == 1


def test_el_dato_puede_estar_fresco_y_el_contraste_muerto(tmp_path: Path):
    """El caso exacto que motivó esta comprobación.

    El relevo de SISAP corre en una máquina que puede estar dormida. Si muere,
    el snapshot se sigue publicando puntual —la tubería de AWS no depende de
    él— y solo desaparece la insignia de verificación. Nada envejece, nada
    falla, y la señal de "segunda fuente lo confirma" se apaga sin ruido.
    """
    r = frescura.revisar(
        _snapshot(tmp_path, "2026-08-20", {"fecha": "2026-08-10", "vigente": False}),
        hoy=date(2026, 8, 20))
    assert r["ok"], "el dato está fresco: el pipeline principal no falló"
    assert not r["verificacion_ok"], "pero el contraste lleva días muerto"


def test_sin_bloque_de_verificacion_se_reporta_sin_afirmar_que_este_roto(tmp_path: Path):
    """Un snapshot recién reconstruido tampoco trae el bloque.

    Tratar la ausencia como avería daría falsos positivos justo después de una
    reconstrucción, así que se reporta y se deja que el umbral de días decida.
    """
    r = frescura.revisar(_snapshot(tmp_path, "2026-08-20"), hoy=date(2026, 8, 20))
    assert r["ok"]
    assert not r["verificacion_ok"]
    assert r["verificacion_dias_de_atraso"] is None
    assert "no trae bloque" in r["detalle_verificacion"]


def test_el_umbral_del_contraste_es_mas_laxo_que_el_del_dato(tmp_path: Path):
    """Dos días sin contraste es mala suerte; dos días sin dato es una avería.

    Los umbrales son distintos porque los fallos son de gravedad distinta, y
    empatarlos convertiría un incidente menor en una alarma de las que se
    ignoran.
    """
    assert frescura.UMBRAL_VERIFICACION_DEFAULT > frescura.UMBRAL_DEFAULT
    r = frescura.revisar(
        _snapshot(tmp_path, "2026-08-20", {"fecha": "2026-08-18"}),
        hoy=date(2026, 8, 20))
    assert r["verificacion_ok"], "2 días hábiles de contraste todavía se toleran"
