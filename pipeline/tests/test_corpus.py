"""Pruebas del corpus RAG (preciovivo.corpus).

Auto-contenidas: sin red, sin claves, sin modelo. El corpus es una función pura
de los hechos del snapshot, así que todo se verifica con datos sintéticos.

Lo que más importa aquí es el DETERMINISMO: si el texto de un chunk cambiara
entre reconstrucciones, la partición histórico/reciente del índice dejaría de
funcionar y habría que reescribir el índice entero cada día.
"""
from __future__ import annotations

import pytest

from preciovivo import corpus as C


# --------------------------------------------------------------------------- #
# Determinismo — la propiedad de la que depende la partición del índice
# --------------------------------------------------------------------------- #
def test_corpus_es_determinista(snapshot_sintetico):
    a = C.build_corpus(snapshot_sintetico, "semana")
    b = C.build_corpus(snapshot_sintetico, "semana")
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.texto for c in a] == [c.texto for c in b]


def test_texto_no_lleva_marca_de_tiempo(snapshot_sintetico):
    """El texto no puede depender de CUÁNDO se generó, solo de los hechos.

    Si llevara la fecha de generación, cada reconstrucción cambiaría todos los
    chunks y el corpus histórico dejaría de ser inmutable.
    """
    chunks = C.build_corpus(snapshot_sintetico, "semana")
    generado = snapshot_sintetico["generatedAt"][:10]
    assert not any(generado in c.texto for c in chunks)


def test_ids_unicos(snapshot_sintetico):
    for gran in C.GRANULARIDADES:
        chunks = C.build_corpus(snapshot_sintetico, gran)
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids)), f"ids repetidos con granularidad={gran}"


def test_rango_de_fechas_coherente(snapshot_sintetico):
    for c in C.build_corpus(snapshot_sintetico, "semana"):
        if c.fecha_inicio and c.fecha_fin:
            assert c.fecha_inicio <= c.fecha_fin


# --------------------------------------------------------------------------- #
# Granularidad
# --------------------------------------------------------------------------- #
def test_granularidad_solo_afecta_a_los_chunks_de_periodo(snapshot_sintetico):
    conteos = {}
    for gran in C.GRANULARIDADES:
        chunks = C.build_corpus(snapshot_sintetico, gran)
        por_tipo: dict[str, int] = {}
        for c in chunks:
            por_tipo[c.tipo] = por_tipo.get(c.tipo, 0) + 1
        conteos[gran] = por_tipo
    # perfil y mercado-dia no dependen de la granularidad
    assert (conteos["dia"][C.TIPO_PRODUCTO_PERFIL]
            == conteos["semana"][C.TIPO_PRODUCTO_PERFIL]
            == conteos["mes"][C.TIPO_PRODUCTO_PERFIL])
    assert (conteos["dia"][C.TIPO_MERCADO_DIA]
            == conteos["semana"][C.TIPO_MERCADO_DIA]
            == conteos["mes"][C.TIPO_MERCADO_DIA])
    # y los de periodo sí, de más fino a más grueso
    assert (conteos["dia"][C.TIPO_PRODUCTO_PERIODO]
            > conteos["semana"][C.TIPO_PRODUCTO_PERIODO]
            > conteos["mes"][C.TIPO_PRODUCTO_PERIODO])


def test_granularidad_invalida():
    with pytest.raises(ValueError):
        C.build_corpus({"productos": []}, "trimestre")


@pytest.mark.parametrize("fecha,gran,esperado", [
    ("2026-08-07", "dia", "2026-08-07"),
    ("2026-08-07", "mes", "2026-08"),
    ("2026-08-07", "semana", "2026-W32"),
    # Borde ISO: el 30-dic-2024 pertenece a la semana 1 del AÑO ISO 2025.
    ("2024-12-30", "semana", "2025-W01"),
    ("2021-01-01", "semana", "2020-W53"),
])
def test_periodo_key(fecha, gran, esperado):
    assert C.periodo_key(fecha, gran) == esperado


# --------------------------------------------------------------------------- #
# Anomalías
# --------------------------------------------------------------------------- #
def test_anomalias_historicas_encuentra_la_inyectada(snapshot_sintetico):
    anoms = C.anomalias_historicas(snapshot_sintetico)
    esperada = snapshot_sintetico["_fecha_anomalia"]
    del_tomate = [a for a in anoms if a["slug"] == "tomate" and a["tipo"] == "precio"]
    assert any(a["fecha"] == esperada for a in del_tomate), (
        f"no se detectó el salto inyectado en {esperada}")


def test_anomalias_historicas_barre_toda_la_serie(snapshot_sintetico):
    """El barrido debe mirar toda la historia, no solo el último día.

    `snapshot.anomalias` viene vacío en el fixture; si el corpus dependiera de él
    no encontraría nada.
    """
    assert snapshot_sintetico["anomalias"] == []
    assert C.anomalias_historicas(snapshot_sintetico)


def test_anomalias_destacadas_acota_por_producto():
    anomalias = [
        {"slug": "papa-blanca", "nombre": "Papa Blanca", "tipo": "precio",
         "fecha": f"2026-01-{d:02d}", "z": float(d), "detalle": "x"}
        for d in range(1, 21)
    ]
    sel = C.anomalias_destacadas(anomalias, fecha_ultima=None, top_por_producto=5)
    assert len(sel) == 5
    # se queda con las de |z| mayor
    assert {a["z"] for a in sel} == {20.0, 19.0, 18.0, 17.0, 16.0}


def test_anomalias_destacadas_incluye_siempre_el_ultimo_dia():
    """Aunque su z sea bajo: el corpus debe coincidir con lo que el dashboard
    llama 'hoy'."""
    anomalias = [
        {"slug": "papa-blanca", "nombre": "Papa Blanca", "tipo": "precio",
         "fecha": f"2026-01-{d:02d}", "z": 50.0, "detalle": "x"}
        for d in range(1, 10)
    ] + [
        {"slug": "papa-blanca", "nombre": "Papa Blanca", "tipo": "precio",
         "fecha": "2026-02-01", "z": 3.6, "detalle": "x"},
    ]
    sel = C.anomalias_destacadas(anomalias, fecha_ultima="2026-02-01",
                                 top_por_producto=3)
    assert any(a["fecha"] == "2026-02-01" for a in sel)


def test_anomalias_destacadas_sin_duplicados():
    a = {"slug": "s", "nombre": "N", "tipo": "precio", "fecha": "2026-02-01",
         "z": 99.0, "detalle": "x"}
    sel = C.anomalias_destacadas([a], fecha_ultima="2026-02-01")
    assert len(sel) == 1


# --------------------------------------------------------------------------- #
# Correlaciones
# --------------------------------------------------------------------------- #
def test_correlaciones_exigen_observaciones_suficientes():
    """Con pocas fechas solapadas no se reporta correlación: sería ruido."""
    productos = [
        {"slug": "a", "nombre": "A", "series": [
            {"fecha": f"2026-01-{d:02d}", "precio_kg": 1.0 + d} for d in range(1, 6)]},
        {"slug": "b", "nombre": "B", "series": [
            {"fecha": f"2026-01-{d:02d}", "precio_kg": 2.0 + d} for d in range(1, 6)]},
    ]
    assert C.correlaciones(productos) == {"a": [], "b": []}


def test_correlaciones_son_simetricas(snapshot_sintetico):
    corr = C.correlaciones(snapshot_sintetico["productos"], top=10)
    for slug, pares in corr.items():
        for otro, r, _ in pares:
            inverso = {o: rr for o, rr, _ in corr[otro]}
            assert otro != slug
            assert inverso[slug] == pytest.approx(r, abs=1e-9)


# --------------------------------------------------------------------------- #
# Contenido del texto: no inventar, sí incluir lo verificable
# --------------------------------------------------------------------------- #
def test_no_inventa_categoria_cuando_es_nula(snapshot_sintetico):
    """`categoria` es NULL en todo el catálogo real: el texto debe omitirla."""
    perfiles = [c for c in C.build_corpus(snapshot_sintetico, "semana")
                if c.tipo == C.TIPO_PRODUCTO_PERFIL]
    assert perfiles
    assert not any("Categoría" in c.texto for c in perfiles)


def test_mercado_dia_incluye_extremos_de_precio(snapshot_sintetico):
    """'¿qué está más barato hoy?' es agregada: su respuesta vive en este chunk."""
    dias = [c for c in C.build_corpus(snapshot_sintetico, "semana")
            if c.tipo == C.TIPO_MERCADO_DIA]
    assert dias
    ultimo = max(dias, key=lambda c: c.fecha_fin)
    assert "más barato del día" in ultimo.texto
    assert "más caro del día" in ultimo.texto


def test_perfil_marca_el_pronostico_como_estimacion(snapshot_sintetico):
    """Regla del proyecto: nunca presentar un pronóstico como cifra del reporte."""
    snap = dict(snapshot_sintetico)
    snap["productos"] = [dict(p) for p in snap["productos"]]
    snap["productos"][0]["forecast"] = {
        "metodo": "gbm", "horizonte_dias": 1, "precio_estimado": 2.10,
        "intervalo": [2.0, 2.2], "mae_modelo": 0.1, "mae_baseline": 0.2, "n_obs": 40}
    perfil = next(c for c in C.build_corpus(snap, "semana")
                  if c.id == "producto-perfil:papa-blanca")
    assert "estimación beta" in perfil.texto
    assert "no es una cifra del reporte" in perfil.texto.lower() or \
           "no una cifra del reporte" in perfil.texto.lower()


def test_anomalia_no_afirma_causa(snapshot_sintetico):
    """El chunk debe decir explícitamente que no explica el porqué: si no, el
    modelo puede leer la anomalía como si fuera una causa."""
    eventos = [c for c in C.build_corpus(snapshot_sintetico, "semana")
               if c.tipo == C.TIPO_EVENTO_ANOMALIA]
    assert eventos
    assert all("no dice la causa" in c.texto for c in eventos)


def test_todos_los_chunks_tienen_texto(snapshot_sintetico):
    for gran in C.GRANULARIDADES:
        for c in C.build_corpus(snapshot_sintetico, gran):
            assert c.texto.strip(), f"chunk vacío: {c.id}"
            assert c.mercado


def test_incluir_filtra_tipos(snapshot_sintetico):
    chunks = C.build_corpus(snapshot_sintetico, "semana",
                            incluir=[C.TIPO_PRODUCTO_PERFIL])
    assert chunks
    assert {c.tipo for c in chunks} == {C.TIPO_PRODUCTO_PERFIL}


# --------------------------------------------------------------------------- #
# Carga
# --------------------------------------------------------------------------- #
def test_cargar_snapshot_acepta_dict_y_ruta(tmp_path, snapshot_sintetico):
    import json

    assert C.cargar_snapshot(snapshot_sintetico) is snapshot_sintetico
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snapshot_sintetico), encoding="utf-8")
    assert C.cargar_snapshot(str(p))["latestFecha"] == snapshot_sintetico["latestFecha"]


def test_cargar_snapshot_rechaza_tipos_raros():
    with pytest.raises(TypeError):
        C.cargar_snapshot(42)
