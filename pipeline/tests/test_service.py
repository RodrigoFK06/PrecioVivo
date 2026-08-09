"""Pruebas de la capa de servicio (preciovivo.service).

Es la capa que comparten API REST, servidor MCP y agente, así que un fallo acá
se propaga a los tres. Lo que más se prueba es la RESOLUCIÓN DE PRODUCTO y el
etiquetado de las salidas del modelo: lo primero porque es el punto de entrada de
todos los transportes, lo segundo porque un pronóstico sin advertencia se
convierte en "el precio de mañana" en el primer reenvío.
"""
from __future__ import annotations

import pytest

from preciovivo import service
from preciovivo.service import ErrorDeConsulta


# --------------------------------------------------------------------------- #
# Resolución de producto
# --------------------------------------------------------------------------- #
def test_resuelve_por_slug_y_por_nombre(snapshot_en_disco):
    assert service.resolver_producto("papa-blanca")["slug"] == "papa-blanca"
    assert service.resolver_producto("Papa Blanca")["slug"] == "papa-blanca"


def test_resuelve_sin_acentos_ni_mayusculas(snapshot_en_disco):
    assert service.resolver_producto("PAPAYA")["slug"] == "papaya"


def test_producto_inexistente_sugiere(snapshot_en_disco):
    """Un 404 pelado obliga a adivinar el slug; con sugerencias se corrige solo."""
    with pytest.raises(ErrorDeConsulta) as e:
        service.resolver_producto("salmon")
    assert "No existe" in str(e.value)


def test_producto_ambiguo_lista_las_opciones(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta) as e:
        service.resolver_producto("papa")
    msg = str(e.value)
    assert "coincide con" in msg
    assert "papa-blanca" in msg and "papa-amarilla" in msg


def test_coincidencia_parcial_unica_resuelve(snapshot_en_disco):
    assert service.resolver_producto("amarilla")["slug"] == "papa-amarilla"


def test_producto_vacio(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta):
        service.resolver_producto("")


# --------------------------------------------------------------------------- #
# Validación de fechas
# --------------------------------------------------------------------------- #
def test_fecha_mal_formada(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta, match="ISO"):
        service.serie_historica("tomate", desde="05/01/2026")


def test_rango_invertido(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta, match="posterior"):
        service.serie_historica("tomate", desde="2026-03-01", hasta="2026-01-01")


# --------------------------------------------------------------------------- #
# Operaciones
# --------------------------------------------------------------------------- #
def test_meta_describe_la_cobertura(snapshot_en_disco, snapshot_sintetico):
    m = service.meta()
    assert m["productos"] == snapshot_sintetico["productCount"]
    assert m["ultima_fecha"] == snapshot_sintetico["latestFecha"]
    assert m["dias_con_datos"] == len(snapshot_sintetico["fechas"])


def test_listar_y_filtrar(snapshot_en_disco):
    assert len(service.listar_productos()) == 4
    assert [p["slug"] for p in service.listar_productos("papaya")] == ["papaya"]


def test_precio_actual_lleva_atribucion(snapshot_en_disco):
    """Quien consume por API o MCP no ve el disclaimer del dashboard."""
    d = service.precio_actual("tomate")
    assert d["precio_kg"] is not None
    assert "referenciales" in d["atribucion"]


def test_serie_respeta_el_rango(snapshot_en_disco, snapshot_sintetico):
    fechas = snapshot_sintetico["fechas"]
    d = service.serie_historica("tomate", desde=fechas[5], hasta=fechas[10])
    assert d["n_puntos"] == 6
    assert d["desde"] == fechas[5] and d["hasta"] == fechas[10]
    assert d["resumen"]["min_kg"] <= d["resumen"]["max_kg"]


def test_serie_sin_rango_devuelve_todo(snapshot_en_disco, snapshot_sintetico):
    assert service.serie_historica("tomate")["n_puntos"] == len(snapshot_sintetico["fechas"])


def test_pronostico_sin_datos_lo_dice(snapshot_en_disco):
    d = service.pronostico("tomate")
    assert d["disponible"] is False
    assert "motivo" in d


def test_pronostico_siempre_lleva_advertencia(snapshot_en_disco, snapshot_sintetico):
    """La regla del proyecto: nunca presentar una estimación como cifra oficial."""
    import json

    snap = json.loads(snapshot_en_disco.read_text(encoding="utf-8"))
    snap["productos"][0]["forecast"] = {
        "metodo": "gbm", "horizonte_dias": 1, "precio_estimado": 2.10,
        "intervalo": [2.0, 2.2], "mae_modelo": 0.1, "mae_baseline": 0.2, "n_obs": 40}
    snapshot_en_disco.write_text(json.dumps(snap), encoding="utf-8")
    service.invalidar_cache()

    d = service.pronostico("papa-blanca")
    assert d["disponible"] is True
    assert d["gana_al_baseline"] is True
    assert "NO una cifra publicada" in d["advertencia"]


def test_anomalias_declaran_que_no_explican_causas(snapshot_en_disco):
    d = service.anomalias()
    assert "NO explicación causal" in d["metodo"]


def test_comparar_ordena_por_precio(snapshot_en_disco):
    d = service.comparar(["papaya", "papa-blanca", "tomate"], dias=10)
    precios = [f["precio_kg"] for f in d["productos"]]
    assert precios == sorted(precios)


def test_comparar_valida_los_limites(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta):
        service.comparar([])
    with pytest.raises(ErrorDeConsulta, match="Máximo 10"):
        service.comparar([f"p{i}" for i in range(11)])


def test_mercados_y_verificacion(snapshot_en_disco):
    assert service.mercados()["principal"]["codigo"] == "GMML"
    assert service.verificacion()["disponible"] is False


def test_resumen_del_dia(snapshot_en_disco, snapshot_sintetico):
    assert service.resumen_dia()["fecha"] == snapshot_sintetico["latestFecha"]


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_el_cache_evita_releer(snapshot_en_disco):
    a = service.snapshot()
    assert service.snapshot() is a  # misma instancia: no releyó


def test_invalidar_cache_relee(snapshot_en_disco):
    a = service.snapshot()
    service.invalidar_cache()
    assert service.snapshot() is not a


def test_sin_snapshot_falla_con_instrucciones(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SNAPSHOT", str(tmp_path / "no-existe.json"))
    service.invalidar_cache()
    with pytest.raises(ErrorDeConsulta, match="preciovivo.export"):
        service.snapshot()
    service.invalidar_cache()


# --------------------------------------------------------------------------- #
# Recuperación
# --------------------------------------------------------------------------- #
def test_recuperar_rechaza_pregunta_vacia(snapshot_en_disco):
    with pytest.raises(ErrorDeConsulta):
        service.recuperar("   ")
