"""Fixtures compartidas para la suite de Precio Vivo.

Hacemos los tests robustos al directorio de ejecución: las rutas de datos
(la base SQLite temporal, el PDF de muestra) se resuelven respecto a la raíz
del paquete `pipeline/`, no respecto al cwd de pytest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from preciovivo.parser import ProductRow

# pipeline/  (este archivo vive en pipeline/tests/conftest.py)
PIPELINE_DIR = Path(__file__).resolve().parent.parent
# Raíz del proyecto y carpeta de PDFs crudos cacheados.
DATA_RAW = PIPELINE_DIR.parent / "data" / "raw"
SAMPLE_PDF = DATA_RAW / "reporte-335_2026-06-22.pdf"


@pytest.fixture
def sample_pdf() -> Path | None:
    """Ruta al PDF de muestra del 2026-06-22, o None si no está cacheado."""
    return SAMPLE_PDF if SAMPLE_PDF.exists() else None


@pytest.fixture
def char_band():
    """Construye una banda de chars (como la que entrega pdfplumber) a partir
    de un string 'pegado' precio_7d+tendencia, p.ej. '4.E1s8table'.

    Cada char lleva su propia x0 creciente para que el orden por x sea el del
    string original, que es justo lo que hace `_split_price_trend`.
    """
    def _build(glued: str, x_start: float = 100.0, step: float = 4.0):
        return [
            {"text": ch, "x0": x_start + i * step, "top": 0.0}
            for i, ch in enumerate(glued)
        ]
    return _build


@pytest.fixture
def make_row():
    """Fábrica de ProductRow sintéticos. Solo `producto` es obligatorio; el
    resto trae valores sanos por defecto. equiv_kg distinto de None hace que
    __post_init__ derive los precios por kg automáticamente.
    """
    def _make(producto: str = "Papa Sintetica", **kw) -> ProductRow:
        base = dict(
            masa_ayer=100.0, masa_hoy=110.0, masa_7d=105.0, masa_4lun=108.0,
            unidad="Kilogramo", equiv_kg=1.0,
            precio_ayer_unit=2.00, precio_hoy_unit=2.50, precio_7d_unit=2.20,
            tendencia="Estable",
        )
        base.update(kw)
        return ProductRow(producto=producto, **base)
    return _make


@pytest.fixture
def snapshot_sintetico():
    """Snapshot mínimo pero realista para la capa RAG.

    Cuatro productos elegidos a propósito:
      - "Papa Blanca" y "Papa Amarilla": comparten primera palabra, para probar
        que "papa" agrupa variedades.
      - "Papaya": empieza igual que "papa" pero es otro producto — prueba que el
        match respeta fronteras de palabra y no las confunde.
      - "Tomate": con un salto de precio inyectado en una fecha conocida, para
        que la detección de anomalías tenga algo verdadero que encontrar.

    Serie de 40 días hábiles desde 2026-01-05 (lunes), que cubre varias semanas
    ISO y dos meses. Todos los valores son deterministas.
    """
    from datetime import date, timedelta

    def dias_habiles(inicio: date, n: int) -> list[str]:
        out, d = [], inicio
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    fechas = dias_habiles(date(2026, 1, 5), 40)
    # Tomate: precio plano en 3.00 y un salto claro en el día 30 (anomalía).
    fecha_anomalia = fechas[30]

    def serie(base: float, anomalia_en: str | None = None, salto: float = 0.0):
        pts = []
        for i, f in enumerate(fechas):
            precio = base + (i % 3) * 0.01  # variación mínima: MAD > 0
            if anomalia_en and f == anomalia_en:
                precio = base + salto
            pts.append({"fecha": f, "precio_kg": round(precio, 4),
                        "masa_hoy": 100.0 + (i % 5), "masa_7d": 102.0,
                        "tendencia": "Estable"})
        return pts

    def producto(nombre, slug, unidad, s):
        ultimo = s[-1]
        anterior = s[-2]["precio_kg"]
        var = round(100.0 * (ultimo["precio_kg"] - anterior) / anterior, 1)
        return {
            "nombre": nombre, "slug": slug, "categoria": None, "unidad": unidad,
            "equiv_kg": 1.0, "series": s,
            "latest": {"fecha": ultimo["fecha"], "precio_kg": ultimo["precio_kg"],
                       "precio_ayer_kg": anterior, "var_pct": var,
                       "masa_hoy": ultimo["masa_hoy"], "masa_7d": ultimo["masa_7d"],
                       "tendencia": ultimo["tendencia"]},
        }

    productos = [
        producto("Papa Blanca", "papa-blanca", "Saco", serie(2.00)),
        producto("Papa Amarilla", "papa-amarilla", "Saco", serie(3.50)),
        producto("Papaya", "papaya", "Kilogramo", serie(4.20)),
        producto("Tomate", "tomate", "Caja",
                 serie(3.00, anomalia_en=fecha_anomalia, salto=5.0)),
    ]
    return {
        "generatedAt": "2026-03-01T00:00:00+00:00",
        "mercado": "Gran Mercado Mayorista de Lima (GMML)",
        "latestFecha": fechas[-1],
        "fechas": fechas,
        "productCount": len(productos),
        # Idéntica a export.ATTRIBUTION: si el fixture recortara el disclaimer,
        # los tests no verificarían la cadena que de verdad se publica.
        "attribution": ("Fuente: MIDAGRI – GMML, procesado por Precio Vivo · "
                        "cifras referenciales, no oficiales."),
        "productos": productos,
        "anomalias": [],
        "_fecha_anomalia": fecha_anomalia,  # ayuda para las aserciones
    }


@pytest.fixture
def snapshot_en_disco(tmp_path, snapshot_sintetico, monkeypatch):
    """Escribe el snapshot sintético y apunta la capa de servicio a él.

    `service` cachea el snapshot por proceso, así que hay que invalidarlo en cada
    test: si no, el primero que corra fija los datos para todos los demás.

    También se aísla el ARTEFACTO RAG. `Recuperador.desde_artefacto` resuelve su
    destino contra `indexer.DESTINO_WEB`, que por defecto es `../web/data`: el
    índice REAL del repositorio. Sin aislarlo, un test de la API pasa o falla
    según qué índice esté commiteado — que es justo lo que un test no debe dejar
    que decida el contenido del repo.

    OJO: no basta `monkeypatch.setenv("PRECIOVIVO_RAG_WEB", ...)`. `DESTINO_WEB`
    se evalúa UNA vez, al importar `indexer`, así que para cuando corre el test
    la variable de entorno ya no se vuelve a leer. Hay que parchear el atributo
    del módulo, que `desde_artefacto` sí resuelve en cada llamada (importa dentro
    de la función).
    """
    import json

    from preciovivo import indexer, service

    ruta = tmp_path / "snapshot.json"
    ruta.write_text(json.dumps(snapshot_sintetico), encoding="utf-8")
    monkeypatch.setenv("PRECIOVIVO_EXPORT", str(ruta))
    monkeypatch.setattr(service, "SNAPSHOT", str(ruta))

    destino_rag = tmp_path / "rag"
    destino_rag.mkdir()
    monkeypatch.setenv("PRECIOVIVO_RAG_WEB", str(destino_rag))
    monkeypatch.setattr(indexer, "DESTINO_WEB", str(destino_rag))

    service.invalidar_cache()
    yield ruta
    service.invalidar_cache()


@pytest.fixture(autouse=True)
def cache_de_embeddings_aislada(tmp_path_factory, monkeypatch):
    """Ningún test escribe en la caché de embeddings REAL.

    `Recuperador.desde_snapshot` pasa por `indexer.embed_con_cache`, así que
    cualquier test que construya un recuperador escribe caché. Con la ruta por
    defecto, la suite —que usa `FakeEmbedder`— sobrescribía la caché del
    proveedor real: pasó de verdad, y costó rehacer 9.206 embeddings ya
    calculados.

    `ruta_para_firma` ya lo hace imposible por construcción (un archivo por
    embebedor). Esto es la segunda barrera: aunque alguien cambie ese esquema,
    los tests siguen sin tocar `data/`. `autouse` porque la protección no debe
    depender de que cada test se acuerde de pedirla.
    """
    from preciovivo import indexer

    destino = tmp_path_factory.mktemp("embed_cache") / "embed_cache.npz"
    monkeypatch.setattr(indexer, "RUTA_CACHE", str(destino))


@pytest.fixture
def embedder_fake():
    """FakeEmbedder: determinista, sin red, sin modelo, sin claves."""
    from preciovivo.embeddings import FakeEmbedder

    return FakeEmbedder(dims=64)


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Store SQLite contra un archivo temporal aislado.

    Forzamos backend SQLite (limpiando DATABASE_URL) y apuntamos PRECIOVIVO_DB
    al tmp_path del test, tal como pide el contrato. El esquema queda inicializado.
    """
    from preciovivo.store import Store

    db_file = tmp_path / "test_preciovivo.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PRECIOVIVO_DB", str(db_file))

    st = Store()
    st.init_schema()
    try:
        yield st
    finally:
        st.close()
