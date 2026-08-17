"""Capa de servicio de Precio Vivo: la lógica que comparten API, MCP y agente.

POR QUÉ ESTE MÓDULO EXISTE
--------------------------
En el bloque 1 quedó una deuda declarada: `web/lib/rag.ts` reimplementa en
TypeScript el parseo de la pregunta porque el sitio no tenía a quién preguntarle.
Dos implementaciones de las mismas reglas divergen en cuanto se toca una.

Ahora hay tres consumidores más —API REST, servidor MCP y agente— y si cada uno
hablara directo con `retrieval`/`corpus`/`forecast`, serían cuatro copias de la
misma lógica de resolución de producto, rangos de fecha y formato de respuesta.

Así que las operaciones de negocio viven ACÁ, en funciones puras que no saben
nada de HTTP, de MCP ni de LangGraph. Cada transporte es una capa delgada encima.

DECISIÓN DE FUENTE
------------------
Se lee del snapshot, no de SQLite, por la misma razón que el corpus (ver
`corpus.py`): es función pura de un artefacto versionado, y garantiza que la API,
el MCP, el agente, la CLI y el dashboard digan exactamente lo mismo. Un endpoint
que contradiga al gráfico de al lado destruye la confianza en ambos.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .corpus import cargar_snapshot

# Ruta del snapshot y TTL del cache en memoria. El pipeline lo reescribe una vez
# al día; releer el JSON de 3,4 MB en cada request sería absurdo, pero cachearlo
# para siempre dejaría un proceso largo sirviendo datos viejos tras la ingesta.
SNAPSHOT = os.environ.get("PRECIOVIVO_EXPORT", "../web/data/snapshot.json")
CACHE_TTL = int(os.environ.get("PRECIOVIVO_CACHE_TTL", "300"))


class ErrorDeConsulta(Exception):
    """Pedido inválido: producto inexistente, fecha mal formada, etc.

    Se distingue de un fallo interno a propósito: cada transporte lo traduce a lo
    suyo (404/400 en HTTP, error de herramienta en MCP) sin tener que adivinar si
    la culpa fue del cliente o nuestra.
    """


# --------------------------------------------------------------------------- #
# Cache del snapshot
# --------------------------------------------------------------------------- #
@dataclass
class _Cache:
    datos: dict | None = None
    cargado_en: float = 0.0
    ruta: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_cache = _Cache()


def snapshot(ruta: str | None = None, forzar: bool = False) -> dict:
    """Snapshot cacheado en memoria con TTL. Seguro entre hilos."""
    ruta = ruta or SNAPSHOT
    with _cache.lock:
        vencido = (time.monotonic() - _cache.cargado_en) > CACHE_TTL
        if forzar or _cache.datos is None or _cache.ruta != ruta or vencido:
            p = Path(ruta)
            if not p.exists():
                raise ErrorDeConsulta(
                    f"No hay snapshot en {ruta}. Genera uno con "
                    f"`python -m preciovivo.export`.")
            _cache.datos = cargar_snapshot(str(p))
            _cache.cargado_en = time.monotonic()
            _cache.ruta = ruta
        return _cache.datos


def invalidar_cache() -> None:
    """Descarta el snapshot cacheado Y el recuperador construido a partir de él.

    El recuperador entra acá porque se deriva del snapshot: dejarlo vivo después
    de invalidar el snapshot dejaría al RAG respondiendo sobre un dataset que ya
    no es el vigente, en silencio.

    Es también lo que hace herméticos a los tests: `_recuperador` es un global de
    proceso, así que sin esto el primer test que lograra construirlo se lo fijaría
    a todos los que corrieran después.
    """
    global _recuperador
    with _cache.lock:
        _cache.datos = None
    # Locks tomados en secuencia, nunca anidados: no hay orden de adquisición que
    # pueda cruzarse con `recuperador()` y trabarse.
    with _lock_rec:
        _recuperador = None


# --------------------------------------------------------------------------- #
# Resolución de producto — una sola definición para todos los transportes
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower().strip()


def resolver_producto(consulta: str, snap: dict | None = None) -> dict:
    """Devuelve el producto por slug o nombre, tolerante a acentos y mayúsculas.

    Lanza `ErrorDeConsulta` con SUGERENCIAS cuando no encuentra: un 404 pelado
    obliga a quien integra a adivinar el slug correcto, y el catálogo tiene 73
    nombres con variantes ("Ajo Criollo O Napuri") que nadie escribe enteros.
    """
    snap = snap or snapshot()
    productos = snap.get("productos") or []
    q = _norm(consulta)
    if not q:
        raise ErrorDeConsulta("Falta el producto.")

    for p in productos:
        if p["slug"] == q or _norm(p["nombre"]) == q:
            return p
    parciales = [p for p in productos if q in _norm(p["nombre"]) or q in p["slug"]]
    if len(parciales) == 1:
        return parciales[0]
    if parciales:
        raise ErrorDeConsulta(
            f"'{consulta}' coincide con {len(parciales)} productos. "
            f"Sé más específico: {', '.join(p['slug'] for p in parciales[:8])}")

    # Sin coincidencia: sugerir por tokens compartidos antes de rendirse.
    tokens = set(q.split())
    cerca = [p["slug"] for p in productos if tokens & set(_norm(p["nombre"]).split())]
    extra = f" ¿Quisiste decir? {', '.join(cerca[:5])}" if cerca else ""
    raise ErrorDeConsulta(f"No existe el producto '{consulta}'.{extra}")


def _rango(desde: str | None, hasta: str | None) -> tuple[str | None, str | None]:
    for etiqueta, valor in (("desde", desde), ("hasta", hasta)):
        if valor:
            try:
                date.fromisoformat(valor)
            except ValueError as e:
                raise ErrorDeConsulta(
                    f"'{etiqueta}' debe ser una fecha ISO (YYYY-MM-DD), "
                    f"no {valor!r}") from e
    if desde and hasta and desde > hasta:
        raise ErrorDeConsulta(f"'desde' ({desde}) es posterior a 'hasta' ({hasta})")
    return desde, hasta


# --------------------------------------------------------------------------- #
# Operaciones de negocio
# --------------------------------------------------------------------------- #
def meta(snap: dict | None = None) -> dict:
    """Metadatos del dataset: qué hay, de cuándo, y de dónde salió."""
    snap = snap or snapshot()
    fechas = snap.get("fechas") or []
    return {
        "mercado": snap.get("mercado"),
        "ultima_fecha": snap.get("latestFecha"),
        "primera_fecha": fechas[0] if fechas else None,
        "dias_con_datos": len(fechas),
        "productos": snap.get("productCount"),
        "generado_en": snap.get("generatedAt"),
        "atribucion": snap.get("attribution"),
    }


def listar_productos(buscar: str | None = None, snap: dict | None = None) -> list[dict]:
    snap = snap or snapshot()
    out = []
    for p in snap.get("productos") or []:
        if buscar and _norm(buscar) not in _norm(p["nombre"]):
            continue
        lat = p.get("latest") or {}
        out.append({
            "slug": p["slug"], "nombre": p["nombre"], "unidad": p.get("unidad"),
            "precio_kg": lat.get("precio_kg"), "var_pct": lat.get("var_pct"),
            "tendencia": lat.get("tendencia"), "fecha": lat.get("fecha"),
        })
    return out


def precio_actual(producto: str, snap: dict | None = None) -> dict:
    """Precio vigente de un producto, con su contexto inmediato."""
    snap = snap or snapshot()
    p = resolver_producto(producto, snap)
    lat = p.get("latest") or {}
    return {
        "slug": p["slug"], "nombre": p["nombre"], "unidad": p.get("unidad"),
        "equiv_kg": p.get("equiv_kg"), "fecha": lat.get("fecha"),
        "precio_kg": lat.get("precio_kg"), "precio_ayer_kg": lat.get("precio_ayer_kg"),
        "var_pct": lat.get("var_pct"), "masa_hoy_t": lat.get("masa_hoy"),
        "masa_7d_t": lat.get("masa_7d"), "tendencia": lat.get("tendencia"),
        "mercado": snap.get("mercado"), "atribucion": snap.get("attribution"),
    }


def serie_historica(producto: str, desde: str | None = None, hasta: str | None = None,
                    snap: dict | None = None) -> dict:
    snap = snap or snapshot()
    p = resolver_producto(producto, snap)
    desde, hasta = _rango(desde, hasta)
    puntos = [
        {"fecha": pt["fecha"], "precio_kg": pt.get("precio_kg"),
         "masa_t": pt.get("masa_hoy"), "tendencia": pt.get("tendencia")}
        for pt in p.get("series") or []
        if (not desde or pt["fecha"] >= desde) and (not hasta or pt["fecha"] <= hasta)
    ]
    precios = [x["precio_kg"] for x in puntos if x["precio_kg"] is not None]
    return {
        "slug": p["slug"], "nombre": p["nombre"], "unidad": p.get("unidad"),
        "desde": puntos[0]["fecha"] if puntos else None,
        "hasta": puntos[-1]["fecha"] if puntos else None,
        "n_puntos": len(puntos),
        "resumen": {
            "min_kg": min(precios) if precios else None,
            "max_kg": max(precios) if precios else None,
            "promedio_kg": round(sum(precios) / len(precios), 4) if precios else None,
        },
        "puntos": puntos,
        "atribucion": snap.get("attribution"),
    }


def pronostico(producto: str, snap: dict | None = None) -> dict:
    """Pronóstico vigente. SIEMPRE marcado como estimación, nunca como dato.

    El campo `advertencia` no es decorativo: quien consuma esto por API o MCP no
    ve el disclaimer del dashboard, y un pronóstico sin etiqueta se convierte en
    "el precio de mañana según Precio Vivo" en el primer reenvío.
    """
    snap = snap or snapshot()
    p = resolver_producto(producto, snap)
    fc = p.get("forecast") or {}
    if not fc.get("precio_estimado"):
        return {
            "slug": p["slug"], "nombre": p["nombre"], "disponible": False,
            "motivo": "sin pronóstico: la serie no tiene observaciones suficientes",
        }
    lo, hi = (fc.get("intervalo") or [None, None])[:2]
    mm, mb = fc.get("mae_modelo"), fc.get("mae_baseline")
    return {
        "slug": p["slug"], "nombre": p["nombre"], "disponible": True,
        "horizonte_dias_habiles": fc.get("horizonte_dias"),
        "precio_estimado_kg": fc.get("precio_estimado"),
        "intervalo_kg": [lo, hi], "metodo": fc.get("metodo"),
        "mae_modelo": mm, "mae_baseline": mb,
        "gana_al_baseline": (mm < mb) if (mm is not None and mb is not None) else None,
        "n_observaciones": fc.get("n_obs"),
        "advertencia": ("Estimación beta de un modelo estadístico, NO una cifra "
                        "publicada por la fuente. No usar para decisiones "
                        "comerciales sin validación propia."),
    }


def anomalias(producto: str | None = None, limite: int = 20,
              snap: dict | None = None) -> dict:
    """Anomalías del último día (las mismas que muestra el dashboard).

    Para el histórico completo está `corpus.anomalias_historicas`, que barre toda
    la serie; acá se devuelve lo vigente, que es lo que el snapshot trae.
    """
    snap = snap or snapshot()
    items = list(snap.get("anomalias") or [])
    if producto:
        slug = resolver_producto(producto, snap)["slug"]
        items = [a for a in items if a.get("slug") == slug]
    items.sort(key=lambda a: abs(a.get("z") or 0), reverse=True)
    return {
        "fecha": snap.get("latestFecha"),
        "n": len(items),
        "metodo": ("z-score robusto sobre mediana y MAD, ventana de 30 datos, "
                   "umbral 3.5. Señal estadística de desvío, NO explicación causal."),
        "anomalias": items[:limite],
    }


def comparar(productos: list[str], dias: int = 30, snap: dict | None = None) -> dict:
    """Compara varios productos sobre la misma ventana temporal."""
    snap = snap or snapshot()
    if not productos:
        raise ErrorDeConsulta("Hay que indicar al menos un producto.")
    if len(productos) > 10:
        raise ErrorDeConsulta("Máximo 10 productos por comparación.")

    filas = []
    for nombre in productos:
        p = resolver_producto(nombre, snap)
        serie = [pt for pt in (p.get("series") or [])
                 if pt.get("precio_kg") is not None][-dias:]
        precios = [pt["precio_kg"] for pt in serie]
        var = None
        if len(precios) >= 2 and precios[0]:
            var = round(100.0 * (precios[-1] - precios[0]) / precios[0], 2)
        filas.append({
            "slug": p["slug"], "nombre": p["nombre"],
            "precio_kg": precios[-1] if precios else None,
            "min_kg": min(precios) if precios else None,
            "max_kg": max(precios) if precios else None,
            "promedio_kg": round(sum(precios) / len(precios), 4) if precios else None,
            "var_pct_ventana": var, "n_puntos": len(precios),
        })
    filas.sort(key=lambda f: (f["precio_kg"] is None, f["precio_kg"]))
    return {"ventana_dias": dias, "hasta": snap.get("latestFecha"), "productos": filas}


def mercados(snap: dict | None = None) -> dict:
    """Mercados además del GMML (frutas MMF2, aves vía SISAP)."""
    snap = snap or snapshot()
    return {
        "principal": {"codigo": "GMML", "nombre": snap.get("mercado"),
                      "ultima_fecha": snap.get("latestFecha"),
                      "n_productos": snap.get("productCount")},
        "adicionales": snap.get("mercados") or [],
    }


def verificacion(snap: dict | None = None) -> dict:
    """Cross-check contra SISAP-MIDAGRI: la segunda opinión sobre nuestra ingesta."""
    snap = snap or snapshot()
    v = snap.get("verificacion")
    if not v:
        return {"disponible": False,
                "motivo": "no hay contraste SISAP cacheado todavía"}
    return {"disponible": True, **v}


def resumen_dia(snap: dict | None = None) -> dict:
    snap = snap or snapshot()
    r = snap.get("resumenIA") or {}
    return {
        "fecha": snap.get("latestFecha"),
        "texto": r.get("texto"),
        # 'llm' = redactado por el modelo sobre hechos ya calculados;
        # 'fallback' = generado por reglas deterministas, sin modelo.
        "fuente": r.get("fuente"),
        "alertas": snap.get("alertas") or [],
    }


# --------------------------------------------------------------------------- #
# Recuperación (RAG) — la operación que el sitio debería consumir
# --------------------------------------------------------------------------- #
_recuperador: Any = None
_lock_rec = threading.Lock()


def recuperador(forzar: bool = False):
    """Recuperador compartido, cargado una vez por proceso.

    Cargarlo por request implicaría releer y des-cuantizar ~9.100 vectores cada
    vez. Se construye perezosamente para que la API arranque aunque no haya
    índice publicado: los endpoints de datos no dependen del RAG.
    """
    global _recuperador
    with _lock_rec:
        if _recuperador is None or forzar:
            from .retrieval import Recuperador
            _recuperador = Recuperador.desde_artefacto(snapshot())
        return _recuperador


def recuperar(pregunta: str, k: int = 8) -> dict:
    """Contexto recuperado SIN llamar al modelo.

    Es la operación que `web/lib/rag.ts` reimplementa hoy en TypeScript. Con esto
    el sitio puede llamar a la MISMA implementación en vez de a una copia — que
    era la deuda declarada del bloque 1.
    """
    if not (pregunta or "").strip():
        raise ErrorDeConsulta("La pregunta está vacía.")
    ctx = recuperador().recuperar(pregunta, k=k)
    return {
        "pregunta": pregunta,
        "interpretacion": {
            "productos": sorted(ctx.consulta.slugs),
            "desde": ctx.consulta.fecha_desde,
            "hasta": ctx.consulta.fecha_hasta,
            "tipos": sorted(ctx.consulta.tipos) if ctx.consulta.tipos else None,
        },
        "n_candidatos": ctx.n_candidatos,
        "garantizado": [{"id": c.id, "tipo": c.tipo, "slug": c.slug,
                         "desde": c.fecha_inicio, "hasta": c.fecha_fin,
                         "texto": c.texto} for c in ctx.piso],
        "recuperado": [{"id": r.chunk.id, "tipo": r.chunk.tipo, "slug": r.chunk.slug,
                        "desde": r.chunk.fecha_inicio, "hasta": r.chunk.fecha_fin,
                        "score": round(r.score, 6), "texto": r.chunk.texto}
                       for r in ctx.recuperados],
        "prompt": ctx.a_prompt(),
    }


def consultar(pregunta: str, k: int = 8) -> dict:
    """Pregunta en lenguaje natural: recupera y hace responder al modelo."""
    from . import ai

    ctx = recuperar(pregunta, k=k)
    snap = snapshot()
    catalogo = [{"slug": p["slug"], "nombre": p["nombre"],
                 "precio_kg": (p.get("latest") or {}).get("precio_kg"),
                 "var_pct": (p.get("latest") or {}).get("var_pct")}
                for p in snap.get("productos") or []]
    res = ai.answer_with_context(pregunta, ctx["prompt"], catalogo)
    return {
        "pregunta": pregunta,
        "respuesta": res.get("texto"),
        # 'llm-rag' = sobre contexto recuperado; 'fallback' = reglas, sin modelo.
        "fuente": res.get("fuente"),
        "productos_citados": res.get("slugs") or [],
        "interpretacion": ctx["interpretacion"],
        "n_chunks_contexto": len(ctx["garantizado"]) + len(ctx["recuperado"]),
        "atribucion": snap.get("attribution"),
    }
