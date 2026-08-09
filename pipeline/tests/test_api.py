"""Pruebas de la API REST (preciovivo.api).

Sin red: FastAPI TestClient llama la app en proceso.

El foco está en la AUTENTICACIÓN, porque es la parte donde un default cómodo se
convierte en un problema de seguridad que nadie revisa hasta que la API lleva
meses pública. El resto de los endpoints se prueba de forma delgada a propósito:
su lógica vive en `service.py` y ya se prueba en `test_service.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from preciovivo.api import CABECERA_API_KEY, app

CLAVE = "clave-de-prueba"
AUTH = {CABECERA_API_KEY: CLAVE}


@pytest.fixture
def cliente(snapshot_en_disco, monkeypatch):
    monkeypatch.setenv("PRECIOVIVO_API_KEYS", CLAVE)
    monkeypatch.delenv("PRECIOVIVO_API_ABIERTA", raising=False)
    return TestClient(app)


@pytest.fixture
def cliente_sin_claves(snapshot_en_disco, monkeypatch):
    monkeypatch.delenv("PRECIOVIVO_API_KEYS", raising=False)
    monkeypatch.delenv("PRECIOVIVO_API_ABIERTA", raising=False)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Autenticación: falla cerrada
# --------------------------------------------------------------------------- #
def test_sin_claves_configuradas_no_sirve_datos(cliente_sin_claves):
    """Un default abierto es la clase de decisión que nadie revisa. Mejor que
    rompa en el primer request de desarrollo, donde se ve."""
    r = cliente_sin_claves.get("/productos")
    assert r.status_code == 503
    assert "PRECIOVIVO_API_KEYS" in r.json()["detail"]


def test_health_responde_aunque_la_puerta_este_cerrada(cliente_sin_claves):
    """Si está cerrada hay que poder preguntarle por qué."""
    r = cliente_sin_claves.get("/health")
    assert r.status_code == 200
    assert r.json()["autenticacion"] == "sin-configurar"


def test_modo_abierto_es_explicito(snapshot_en_disco, monkeypatch):
    monkeypatch.delenv("PRECIOVIVO_API_KEYS", raising=False)
    monkeypatch.setenv("PRECIOVIVO_API_ABIERTA", "1")
    c = TestClient(app)
    assert c.get("/productos").status_code == 200
    assert c.get("/health").json()["autenticacion"] == "abierta"


def test_falta_la_cabecera(cliente):
    assert cliente.get("/productos").status_code == 401


def test_clave_invalida(cliente):
    assert cliente.get("/productos", headers={CABECERA_API_KEY: "otra"}).status_code == 403


def test_clave_valida(cliente):
    assert cliente.get("/productos", headers=AUTH).status_code == 200


def test_varias_claves(snapshot_en_disco, monkeypatch):
    monkeypatch.setenv("PRECIOVIVO_API_KEYS", "a, b ,c")
    c = TestClient(app)
    for k in ("a", "b", "c"):
        assert c.get("/productos", headers={CABECERA_API_KEY: k}).status_code == 200
    assert c.get("/productos", headers={CABECERA_API_KEY: "d"}).status_code == 403


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_reporta_el_estado_real(cliente):
    """Un 200 fijo mientras se sirven datos viejos es peor que estar caído."""
    d = cliente.get("/health").json()
    assert d["estado"] == "ok"
    assert d["dataset"]["ultima_fecha"]


def test_health_sin_snapshot_no_miente(tmp_path, monkeypatch):
    from preciovivo import service

    monkeypatch.setattr(service, "SNAPSHOT", str(tmp_path / "no-existe.json"))
    service.invalidar_cache()
    d = TestClient(app).get("/health").json()
    assert d["estado"] == "sin_datos"
    assert d["detalle"]
    service.invalidar_cache()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def test_catalogo(cliente):
    d = cliente.get("/productos", headers=AUTH).json()
    assert d["n"] == 4
    assert "referenciales" in d["atribucion"]


def test_catalogo_filtrado(cliente):
    d = cliente.get("/productos?buscar=papaya", headers=AUTH).json()
    assert [p["slug"] for p in d["productos"]] == ["papaya"]


def test_producto_por_nombre_con_espacios(cliente):
    r = cliente.get("/productos/Papa Blanca", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["slug"] == "papa-blanca"


def test_producto_inexistente_da_404_con_pista(cliente):
    r = cliente.get("/productos/salmon", headers=AUTH)
    assert r.status_code == 404
    assert "No existe" in r.json()["detail"]


def test_producto_ambiguo_da_404_con_opciones(cliente):
    r = cliente.get("/productos/papa", headers=AUTH)
    assert r.status_code == 404
    assert "papa-blanca" in r.json()["detail"]


def test_serie_con_rango(cliente, snapshot_sintetico):
    f = snapshot_sintetico["fechas"]
    r = cliente.get(f"/productos/tomate/serie?desde={f[0]}&hasta={f[4]}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["n_puntos"] == 5


def test_serie_con_fecha_invalida(cliente):
    assert cliente.get("/productos/tomate/serie?desde=ayer", headers=AUTH).status_code == 404


def test_pronostico(cliente):
    r = cliente.get("/productos/tomate/pronostico", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["disponible"] is False


def test_anomalias(cliente):
    r = cliente.get("/anomalias?limite=5", headers=AUTH)
    assert r.status_code == 200
    assert "NO explicación causal" in r.json()["metodo"]


def test_comparar(cliente):
    r = cliente.get("/comparar?productos=tomate&productos=papaya&dias=10", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()["productos"]) == 2


def test_comparar_valida_el_limite_de_dias(cliente):
    r = cliente.get("/comparar?productos=tomate&dias=99999", headers=AUTH)
    assert r.status_code == 422  # lo rechaza el esquema, no el servicio


def test_meta_mercados_resumen_verificacion(cliente):
    for ruta in ("/meta", "/mercados", "/resumen", "/verificacion"):
        assert cliente.get(ruta, headers=AUTH).status_code == 200, ruta


def test_consulta_sin_indice_da_503(cliente):
    """Índice RAG ausente es un problema de despliegue, no del cliente."""
    r = cliente.post("/consulta?pregunta=hola", headers=AUTH)
    assert r.status_code in (503, 200)
    if r.status_code == 503:
        assert "index" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Contrato OpenAPI
# --------------------------------------------------------------------------- #
def test_openapi_se_genera(cliente):
    d = cliente.get("/openapi.json").json()
    assert d["info"]["title"] == "Precio Vivo API"
    for ruta in ("/productos", "/productos/{producto}", "/anomalias", "/consulta"):
        assert ruta in d["paths"], ruta


def test_openapi_documenta_las_advertencias(cliente):
    """El contrato tiene que decir que los pronósticos son estimaciones: quien
    integra lee esto, no el README."""
    d = cliente.get("/openapi.json").json()
    assert "estimaciones" in d["info"]["description"].lower()
    etiquetas = {t["name"]: t["description"] for t in d["info"].get("x-tags", [])} \
        or {t["name"]: t["description"] for t in d.get("tags", [])}
    assert "Estimaciones" in etiquetas.get("modelo", "")
