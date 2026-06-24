"""Motor de alertas (Fase 4) de Precio Vivo: SOLO detección y reglas.

Este módulo decide QUÉ situaciones ameritan avisar; NO envía nada. El envío
(email, WhatsApp, push) es un sistema externo y queda deliberadamente FUERA: la
frontera honesta es que aquí termina la lógica de negocio y empieza el emisor.

Modelo:
  - Regla            : watchlist de productos + un tipo de condición con umbral.
  - AlertaDisparada  : un disparo concreto {producto, slug, tipo, mensaje, valor,
                       fecha}; es el contrato de salida de `evaluate`.

Tipos de regla soportados (todos sobre el ÚLTIMO día disponible):
  - "var_pct"   : la variación diaria de precio cruza un umbral (|var%| >= X, o
                  solo subas / solo bajas según `direccion`).
  - "nivel"     : el precio S/ por kg cruza un nivel absoluto (sube por encima de
                  `umbral` o baja por debajo, según `direccion`).
  - "anomalia"  : reutiliza ai.detecta_anomalias (z-score robusto); dispara si
                  |z| >= `umbral` para los productos de la watchlist.

`evaluate(origen, reglas)` acepta:
  - un dict snapshot.json ya cargado (lo normal; lo arma export.build),
  - una ruta a la BD SQLite (str): construye el snapshot vía export.build.

API pública (funciones/datos puros, sin efectos externos):
  - DEFAULT_REGLAS                              -> list[Regla] razonable por defecto
  - evaluate(origen, reglas=None)               -> list[AlertaDisparada]
  - carga_para_emisor(alertas, canal=...)       -> dict (payload listo para enviar)

Honestidad: el módulo no inventa números; todo valor reportado proviene del
snapshot (precio, var%, z). No predice ni decide cómo se entrega el aviso.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .export import slugify

# Tipos de regla reconocidos (cualquier otro se ignora con seguridad).
TIPO_VAR_PCT = "var_pct"
TIPO_NIVEL = "nivel"
TIPO_ANOMALIA = "anomalia"
TIPOS_VALIDOS = (TIPO_VAR_PCT, TIPO_NIVEL, TIPO_ANOMALIA)

# Direcciones de cruce (para var_pct y nivel).
DIR_SUBE = "sube"      # solo alzas / cruces hacia arriba
DIR_BAJA = "baja"      # solo caídas / cruces hacia abajo
DIR_AMBAS = "ambas"    # cualquier dirección (|valor| >= umbral)


# --------------------------------------------------------------------------- #
# Modelo de datos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Regla:
    """Una regla de alerta: a qué productos aplica y con qué condición/umbral.

    Campos:
      tipo       : 'var_pct' | 'nivel' | 'anomalia'.
      umbral     : valor de corte. var_pct -> puntos porcentuales (p.ej. 15.0);
                   nivel -> soles por kg; anomalia -> |z| mínimo.
      direccion  : 'sube' | 'baja' | 'ambas' (no aplica a 'anomalia').
      watchlist  : lista de nombres o slugs de productos a vigilar. Vacía o
                   {"*"} = todos los productos. El match es por slug (tolerante a
                   acentos/mayúsculas vía export.slugify).
      nombre     : etiqueta legible de la regla (para trazabilidad en el payload).
    """
    tipo: str
    umbral: float
    direccion: str = DIR_AMBAS
    watchlist: frozenset[str] = field(default_factory=frozenset)
    nombre: str = ""

    def __post_init__(self) -> None:
        if self.tipo not in TIPOS_VALIDOS:
            raise ValueError(
                f"tipo de regla inválido: {self.tipo!r} (use uno de {TIPOS_VALIDOS})")
        if self.direccion not in (DIR_SUBE, DIR_BAJA, DIR_AMBAS):
            raise ValueError(f"direccion inválida: {self.direccion!r}")

    def aplica_a(self, slug: str) -> bool:
        """True si esta regla vigila el producto `slug` (vacía/'*' = todos)."""
        if not self.watchlist or "*" in self.watchlist:
            return True
        return slug in self._slugs_watchlist()

    def _slugs_watchlist(self) -> set[str]:
        """Normaliza la watchlist a slugs (acepta nombres o slugs ya hechos)."""
        return {w if w == "*" else slugify(w) for w in self.watchlist}


@dataclass(frozen=True)
class AlertaDisparada:
    """Una alerta concreta que se dispararía. Contrato de salida de `evaluate`.

    Campos:
      producto : nombre canónico del producto.
      slug     : slug del producto (export.slugify).
      tipo     : tipo de la regla que disparó ('var_pct'|'nivel'|'anomalia').
      mensaje  : texto en español, listo para mostrar/enviar (sin emojis).
      valor    : magnitud que disparó (var% , precio S/kg o z, según el tipo).
      fecha    : fecha del dato que disparó (YYYY-MM-DD).
      regla    : etiqueta de la regla de origen (trazabilidad).
    """
    producto: str
    slug: str
    tipo: str
    mensaje: str
    valor: float
    fecha: str
    regla: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Reglas por defecto (set razonable; honesto y conservador)
# --------------------------------------------------------------------------- #
# Umbrales pensados para el GMML: la variación diaria típica es de pocos puntos,
# así que ±15% ya es un movimiento fuerte digno de aviso. La anomalía reusa el
# umbral estadístico del propio detector (ai.ANOMALIA_Z).
def _default_reglas() -> list[Regla]:
    return [
        Regla(
            tipo=TIPO_VAR_PCT, umbral=15.0, direccion=DIR_AMBAS,
            nombre="Variación diaria fuerte (>=15%)",
        ),
        Regla(
            tipo=TIPO_ANOMALIA, umbral=3.5, direccion=DIR_AMBAS,
            nombre="Anomalía estadística (|z|>=3.5)",
        ),
    ]


DEFAULT_REGLAS: list[Regla] = _default_reglas()


# --------------------------------------------------------------------------- #
# Carga del snapshot (desde dict ya armado o desde la BD vía export.build)
# --------------------------------------------------------------------------- #
def _resolver_snapshot(origen: Any) -> dict:
    """Devuelve un snapshot dict a partir de `origen`.

    `origen` puede ser:
      - dict con 'productos'  -> se usa tal cual,
      - str (ruta a SQLite)   -> se arma con export.build (incluye anomalías),
      - None                  -> usa PRECIOVIVO_DB / default y arma con export.build.
    """
    if isinstance(origen, dict) and "productos" in origen:
        return origen
    if origen is None or isinstance(origen, str):
        from .export import build
        db = origen or os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
        return build(db)
    raise TypeError("origen debe ser un snapshot dict, una ruta SQLite (str) o None")


# --------------------------------------------------------------------------- #
# Evaluadores por tipo de regla (puros; reciben el snapshot ya resuelto)
# --------------------------------------------------------------------------- #
def _fmt_soles(x: Any) -> str:
    try:
        return f"S/ {float(x):.2f}"
    except (TypeError, ValueError):
        return "S/ —"


def _eval_var_pct(snapshot: dict, regla: Regla) -> list[AlertaDisparada]:
    """Dispara cuando la variación diaria de precio cruza el umbral (en pp)."""
    out: list[AlertaDisparada] = []
    for p in snapshot.get("productos", []):
        slug = p.get("slug") or slugify(p.get("nombre", ""))
        if not regla.aplica_a(slug):
            continue
        lat = p.get("latest") or {}
        var = lat.get("var_pct")
        if var is None:
            continue
        var = float(var)
        if not _cruza_umbral(var, regla.umbral, regla.direccion):
            continue
        sentido = "subió" if var > 0 else "bajó"
        out.append(AlertaDisparada(
            producto=p.get("nombre", ""),
            slug=slug,
            tipo=TIPO_VAR_PCT,
            mensaje=(
                f"{p.get('nombre','')} {sentido} {abs(var):.1f}% en un día, a "
                f"{_fmt_soles(lat.get('precio_kg'))}/kg (umbral {regla.umbral:.0f}%)."
            ),
            valor=round(var, 2),
            fecha=lat.get("fecha", snapshot.get("latestFecha", "")) or "",
            regla=regla.nombre,
        ))
    return out


def _eval_nivel(snapshot: dict, regla: Regla) -> list[AlertaDisparada]:
    """Dispara cuando el precio S/kg del día cruza un nivel absoluto.

    direccion='sube' -> precio >= umbral; 'baja' -> precio <= umbral;
    'ambas' -> cualquiera de las dos (raro para nivel, pero soportado).
    """
    out: list[AlertaDisparada] = []
    for p in snapshot.get("productos", []):
        slug = p.get("slug") or slugify(p.get("nombre", ""))
        if not regla.aplica_a(slug):
            continue
        lat = p.get("latest") or {}
        precio = lat.get("precio_kg")
        if precio is None:
            continue
        precio = float(precio)
        cruza = (
            (regla.direccion in (DIR_SUBE, DIR_AMBAS) and precio >= regla.umbral)
            or (regla.direccion in (DIR_BAJA, DIR_AMBAS) and precio <= regla.umbral)
        )
        if not cruza:
            continue
        rel = "por encima de" if precio >= regla.umbral else "por debajo de"
        out.append(AlertaDisparada(
            producto=p.get("nombre", ""),
            slug=slug,
            tipo=TIPO_NIVEL,
            mensaje=(
                f"{p.get('nombre','')} está a {_fmt_soles(precio)}/kg, "
                f"{rel} el nivel de referencia {_fmt_soles(regla.umbral)}/kg."
            ),
            valor=round(precio, 4),
            fecha=lat.get("fecha", snapshot.get("latestFecha", "")) or "",
            regla=regla.nombre,
        ))
    return out


def _eval_anomalia(snapshot: dict, regla: Regla) -> list[AlertaDisparada]:
    """Dispara reutilizando ai.detecta_anomalias (z-score robusto, sin LLM).

    Usa las anomalías ya presentes en el snapshot si existen (snapshot.anomalias);
    si no, las calcula sobre el propio snapshot. Filtra por |z| >= umbral y por
    la watchlist de la regla.
    """
    from .ai import detecta_anomalias

    anomalias = snapshot.get("anomalias")
    if anomalias is None:
        anomalias = detecta_anomalias(snapshot)

    out: list[AlertaDisparada] = []
    for a in anomalias:
        slug = a.get("slug") or slugify(a.get("nombre", ""))
        if not regla.aplica_a(slug):
            continue
        try:
            z = float(a.get("z"))
        except (TypeError, ValueError):
            continue
        if abs(z) < regla.umbral:
            continue
        tipo_anom = a.get("tipo", "precio")  # 'precio' | 'volumen'
        out.append(AlertaDisparada(
            producto=a.get("nombre", ""),
            slug=slug,
            tipo=TIPO_ANOMALIA,
            mensaje=(
                f"Anomalía de {tipo_anom} en {a.get('nombre','')}: "
                f"se desvió z={z:+.1f} de su historia reciente "
                f"({a.get('detalle','')}) el {a.get('fecha','')}."
            ),
            valor=round(z, 2),
            fecha=a.get("fecha", "") or "",
            regla=regla.nombre,
        ))
    return out


_EVALUADORES = {
    TIPO_VAR_PCT: _eval_var_pct,
    TIPO_NIVEL: _eval_nivel,
    TIPO_ANOMALIA: _eval_anomalia,
}


def _cruza_umbral(valor: float, umbral: float, direccion: str) -> bool:
    """¿`valor` cruza `umbral` en la `direccion` dada? (para var_pct)."""
    if direccion == DIR_SUBE:
        return valor >= umbral
    if direccion == DIR_BAJA:
        return valor <= -abs(umbral)
    return abs(valor) >= abs(umbral)


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def evaluate(origen: Any, reglas: list[Regla] | None = None) -> list[AlertaDisparada]:
    """Evalúa todas las reglas sobre el ÚLTIMO día y devuelve las alertas.

    `origen`: snapshot dict, ruta a SQLite (str) o None (usa PRECIOVIVO_DB).
    `reglas`: lista de Regla; si es None se usan DEFAULT_REGLAS.

    Devuelve list[AlertaDisparada] deduplicada (mismo slug+tipo+regla se colapsa)
    y ordenada por |valor| descendente — los movimientos más extremos primero.
    No envía nada; solo detecta.
    """
    snapshot = _resolver_snapshot(origen)
    reglas = DEFAULT_REGLAS if reglas is None else reglas

    disparadas: list[AlertaDisparada] = []
    for regla in reglas:
        evaluador = _EVALUADORES.get(regla.tipo)
        if evaluador is None:  # tipo desconocido: se ignora con seguridad
            continue
        disparadas.extend(evaluador(snapshot, regla))

    # Dedup por (slug, tipo, regla) conservando el primero visto.
    vistos: set[tuple[str, str, str]] = set()
    unicas: list[AlertaDisparada] = []
    for a in disparadas:
        clave = (a.slug, a.tipo, a.regla)
        if clave in vistos:
            continue
        vistos.add(clave)
        unicas.append(a)

    unicas.sort(key=lambda a: abs(a.valor), reverse=True)
    return unicas


def carga_para_emisor(
    alertas: list[AlertaDisparada],
    canal: str = "generico",
    snapshot: dict | None = None,
) -> dict:
    """Empaqueta las alertas en una 'carga lista para enviar' (sin enviar).

    Devuelve un dict autocontenido que un emisor externo (email/WhatsApp/push)
    podría consumir directamente. NO realiza ningún envío ni I/O de red: es la
    frontera entre detección (este módulo) y entrega (sistema externo).

    Estructura:
      {
        "version": 1,
        "canal": str,                 # destino lógico sugerido (informativo)
        "generado_en": str,           # timestamp ISO-8601 UTC
        "mercado": str | None,
        "fecha_datos": str | None,    # latestFecha del snapshot, si se conoce
        "n_alertas": int,
        "asunto": str,                # resumen de una línea (apto como subject)
        "resumen": str,               # texto multilinea legible
        "alertas": [ {AlertaDisparada como dict}, ... ],
      }
    """
    n = len(alertas)
    fecha = snapshot.get("latestFecha") if snapshot else None
    mercado = snapshot.get("mercado") if snapshot else None

    if n == 0:
        asunto = "Precio Vivo: sin alertas en la jornada"
        resumen = "No se dispararon alertas con las reglas vigentes."
    else:
        asunto = (
            f"Precio Vivo: {n} alerta{'s' if n != 1 else ''}"
            + (f" del {fecha}" if fecha else "")
        )
        lineas = [a.mensaje for a in alertas]
        resumen = "\n".join(f"- {ln}" for ln in lineas)

    return {
        "version": 1,
        "canal": canal,
        "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mercado": mercado,
        "fecha_datos": fecha,
        "n_alertas": n,
        "asunto": asunto,
        "resumen": resumen,
        "alertas": [a.to_dict() for a in alertas],
    }


# --------------------------------------------------------------------------- #
# __main__ — imprime las alertas que se dispararían HOY (sin enviar nada)
# --------------------------------------------------------------------------- #
def main() -> None:
    db = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
    snapshot = _resolver_snapshot(db)
    fecha = snapshot.get("latestFecha")
    n_prod = snapshot.get("productCount", len(snapshot.get("productos", [])))

    print(f"== Precio Vivo · motor de alertas (Fase 4) · DB={db} ==")
    print(f"Mercado: {snapshot.get('mercado')}")
    print(f"Fecha de datos: {fecha} · productos: {n_prod}")
    print("Reglas por defecto:")
    for r in DEFAULT_REGLAS:
        print(f"  - [{r.tipo}] {r.nombre} (umbral={r.umbral}, dir={r.direccion})")
    print()

    alertas = evaluate(snapshot, DEFAULT_REGLAS)
    print(f"--- Alertas que se dispararían HOY: {len(alertas)} ---")
    for a in alertas:
        print(f"  [{a.tipo:<8}] {a.producto:<26.26} valor={a.valor:+8.2f} {a.fecha}")
        print(f"             {a.mensaje}")
    if not alertas:
        print("  (ninguna con las reglas vigentes)")
    print()

    carga = carga_para_emisor(alertas, canal="consola", snapshot=snapshot)
    print("--- Carga lista para emisor (NO se envía nada) ---")
    print(f"  asunto      : {carga['asunto']}")
    print(f"  canal       : {carga['canal']}")
    print(f"  n_alertas   : {carga['n_alertas']}")
    print(f"  generado_en : {carga['generado_en']}")


if __name__ == "__main__":
    main()
