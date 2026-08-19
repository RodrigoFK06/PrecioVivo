"""Corpus de recuperación (RAG) de Precio Vivo: hechos numéricos -> texto.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
No tenemos un corpus documental que indexar. El PLAN §0.5 prohíbe almacenar la
prosa de la fuente (solo republicamos HECHOS numéricos), y la BD no trae
descripciones: `categoria` es NULL en los 73 productos y el único texto nativo es
el nombre canónico. Así que el corpus se GENERA desde nuestros propios hechos.

Eso no es una limitación que sorteamos: es lo que define la estrategia. Con 73
productos, recuperar *el producto* por embeddings sería decorativo — un substring
sin acentos ya lo resuelve, y la ruta /api/consulta hoy ya manda el catálogo
entero al modelo. Lo que falta es PROFUNDIDAD TEMPORAL: el modelo ve una línea
por producto (precio de hoy, var vs ayer, tendencia) y por eso no puede contestar
"¿por qué subió la papa esta semana?". La evidencia no está en el contexto.

Entonces el corpus se organiza por VENTANA DE TIEMPO, no por producto:

  producto-semana   producto × semana ISO   ~7.900   "¿por qué subió esta semana?"
  producto-perfil   producto                    73   "¿cuánto suele costar en agosto?"
  evento-anomalia   anomalía z-score        decenas  "¿cuándo hubo saltos raros?"
  mercado-dia       día                        519   "¿cómo estuvo el 15 de julio?"

GRANULARIDAD
------------
`granularidad` es un parámetro (dia|semana|mes) y no una constante a propósito:
la evaluación de recuperación corre los tres granos y el README publica los
números medidos. La hipótesis que se pone a prueba es que el grano diario produce
texto casi idéntico entre días contiguos (embeddings casi duplicados, el top-k se
llena con 5 días seguidos del mismo producto) y el mensual esconde el pico
intra-mes que es justo lo que la pregunta busca.

DETERMINISMO (load-bearing)
---------------------------
El texto de un chunk depende SOLO de los hechos de su ventana. Nada de fechas de
generación ni de estado global. Esto es lo que hace inmutable al corpus histórico
—una semana pasada nunca cambia— y por eso el índice se puede partir en una parte
histórica que se commitea una vez y una cola reciente que cambia a diario. Sin
determinismo, el índice completo se reescribiría cada día (~550 MB/año en git).

FUENTE
------
Se construye desde el snapshot (no desde SQLite) por tres razones: es función pura
de un artefacto ya versionado, ya trae anomalías y pronósticos adjuntos, y
garantiza que lo que el RAG recupera es exactamente lo que el dashboard muestra.
"""
from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

# Tipos de chunk (valores estables: entran en los IDs y en el gold set de evals).
TIPO_PRODUCTO_PERIODO = "producto-periodo"
TIPO_PRODUCTO_PERFIL = "producto-perfil"
TIPO_EVENTO_ANOMALIA = "evento-anomalia"
TIPO_MERCADO_DIA = "mercado-dia"
# Los mercados que NO son el GMML: pollo vivo (AVES, vía SISAP) y lo que llegue
# por el boletín. Van en su propio tipo y no mezclados con los del GMML porque
# no son comparables: otra fuente, otra jornada y, en el caso de las aves, otra
# unidad de negocio. Un chunk POR PRODUCTO, no por mercado, para que el
# pre-filtro estructurado por slug pueda alcanzarlos.
TIPO_OTRO_MERCADO = "otro-mercado-dia"

GRANULARIDADES = ("dia", "semana", "mes")
GRANULARIDAD_DEFAULT = "semana"

# Mínimo de observaciones solapadas para reportar una correlación. Por debajo, el
# coeficiente es ruido y no se publica (misma disciplina que el kill-gate).
MIN_OBS_CORRELACION = 60
# Cuántos productos correlacionados se citan en la ficha del producto.
TOP_CORRELACIONADOS = 5
# Cuántos co-movimientos se citan en el chunk de periodo.
TOP_COMOVIMIENTOS = 3
# Cuántas anomalías por producto y tipo reciben chunk propio. Ver
# `anomalias_destacadas` para por qué no son todas.
TOP_ANOMALIAS_POR_PRODUCTO = 5

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "setiembre", "octubre", "noviembre", "diciembre"]

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


# --------------------------------------------------------------------------- #
# Contrato
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Chunk:
    """Una unidad recuperable del corpus.

    `id` es estable y determinista: mismo hecho -> mismo id entre reconstrucciones.
    De eso dependen el gold set de evaluación y la partición histórico/reciente
    del índice.

    Los campos de metadatos existen para el PRE-FILTRO ESTRUCTURADO de la búsqueda
    híbrida: se aplican como WHERE antes del k-NN, no después.
    """
    id: str
    tipo: str
    texto: str
    slug: str | None
    producto: str | None
    mercado: str
    fecha_inicio: str
    fecha_fin: str

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Formato (compartido por todos los generadores de texto)
# --------------------------------------------------------------------------- #
def _soles(x: Any) -> str:
    try:
        return f"S/ {float(x):.2f}"
    except (TypeError, ValueError):
        return "S/ —"


def _pct(x: Any, decimales: int = 1) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if v > 0 else ''}{v:.{decimales}f}%"


def _toneladas(x: Any) -> str:
    try:
        return f"{float(x):,.0f} t".replace(",", " ")
    except (TypeError, ValueError):
        return "— t"


def _fecha_larga(iso: str) -> str:
    """'2026-08-07' -> 'viernes 7 de agosto de 2026'. Sin locale ni TZ."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "—"
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} de {d.year}"


def _fecha_corta(iso: str) -> str:
    """'2026-08-07' -> '7 de agosto'."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "—"
    return f"{d.day} de {_MESES[d.month - 1]}"


def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


# --------------------------------------------------------------------------- #
# Periodos: la decisión de granularidad, aislada en dos funciones
# --------------------------------------------------------------------------- #
def periodo_key(fecha_iso: str, granularidad: str) -> str:
    """Clave del periodo al que cae `fecha_iso`. Estable y ordenable como string.

    dia    -> '2026-08-07'
    semana -> '2026-W32'   (semana ISO: el año ISO puede diferir del calendario
                            en los bordes de enero, y es lo correcto)
    mes    -> '2026-08'
    """
    if granularidad == "dia":
        return fecha_iso
    if granularidad == "mes":
        return fecha_iso[:7]
    if granularidad == "semana":
        iso = date.fromisoformat(fecha_iso).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"granularidad desconocida: {granularidad!r}")


def periodo_label(key: str, granularidad: str, inicio: str, fin: str) -> str:
    """Etiqueta legible del periodo, para el texto del chunk."""
    if granularidad == "dia":
        return _fecha_larga(inicio)
    if granularidad == "mes":
        anio, mes = key.split("-")
        return f"{_MESES[int(mes) - 1]} de {anio}"
    # semana: se nombra por su rango real de datos (días hábiles), no 'lunes a
    # domingo' — el mercado no publica fines de semana y decir 'al domingo' sería
    # afirmar un dato que no tenemos.
    if inicio == fin:
        return f"semana {key} ({_fecha_corta(inicio)})"
    return f"semana {key} (del {_fecha_corta(inicio)} al {_fecha_corta(fin)})"


# --------------------------------------------------------------------------- #
# Carga del snapshot
# --------------------------------------------------------------------------- #
def cargar_snapshot(origen: Any) -> dict:
    """Acepta ruta a snapshot.json, Path, o el dict ya cargado.

    Mismo patrón poliforma que `ai.detecta_anomalias`, por consistencia.
    """
    if isinstance(origen, dict):
        return origen
    if isinstance(origen, (str, Path)):
        return json.loads(Path(origen).read_text(encoding="utf-8"))
    raise TypeError("origen debe ser ruta a snapshot.json o el dict ya cargado")


# --------------------------------------------------------------------------- #
# Estadística de apoyo (pura, sin dependencias del LLM)
# --------------------------------------------------------------------------- #
def _retornos(serie: list[dict]) -> dict[str, float]:
    """Retornos porcentuales diarios por fecha: {fecha: pct_change}.

    Se usa para correlación. Correlacionar NIVELES de precio es espurio (dos
    series con tendencia correlacionan alto sin relación real); los retornos
    quitan esa tendencia común.
    """
    out: dict[str, float] = {}
    previo: float | None = None
    for pt in serie:
        v = pt.get("precio_kg")
        if v is not None and previo is not None and previo != 0:
            out[pt["fecha"]] = (v - previo) / previo
        if v is not None:
            previo = v
    return out


def _pearson(a: dict[str, float], b: dict[str, float]) -> tuple[float, int] | None:
    """Pearson sobre las fechas solapadas. Devuelve (r, n_obs) o None."""
    comunes = sorted(set(a) & set(b))
    n = len(comunes)
    if n < MIN_OBS_CORRELACION:
        return None
    xs = [a[f] for f in comunes]
    ys = [b[f] for f in comunes]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy), n


def correlaciones(productos: list[dict], top: int = TOP_CORRELACIONADOS
                  ) -> dict[str, list[tuple[str, float, int]]]:
    """{slug: [(slug_otro, r, n_obs), ...]} con las `top` correlaciones más fuertes.

    Sobre retornos diarios, no niveles. Solo pares con >= MIN_OBS_CORRELACION
    fechas solapadas; el resto simplemente no se reporta (no se rellena con ruido).
    """
    rets = {p["slug"]: _retornos(p.get("series") or []) for p in productos}
    slugs = sorted(rets)
    out: dict[str, list[tuple[str, float, int]]] = {s: [] for s in slugs}
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            res = _pearson(rets[a], rets[b])
            if res is None:
                continue
            r, n = res
            out[a].append((b, r, n))
            out[b].append((a, r, n))
    for s in slugs:
        out[s].sort(key=lambda t: abs(t[1]), reverse=True)
        out[s] = out[s][:top]
    return out


def anomalias_historicas(
    snap: dict, ventana: int = 30, z_umbral: float = 3.5,
) -> list[dict]:
    """Barre TODA la historia buscando anomalías, no solo el último día.

    `snapshot.anomalias` solo trae las del último día (es lo que el dashboard
    necesita). Para el corpus eso deja fuera dos años de eventos, y "¿cuándo hubo
    saltos raros en la papa?" no tendría qué recuperar.

    Reusa `ai._robust_z` a propósito: si el corpus calculara la anomalía de otra
    forma que el dashboard, ambos dirían cosas distintas del mismo día. Un solo
    estadístico, dos consumidores.

    Devuelve el mismo contrato que `ai.detecta_anomalias`:
      [{slug, nombre, tipo:'precio'|'volumen', fecha, z, detalle}]
    """
    from .ai import _robust_z  # mismo paquete: una sola definición del estadístico

    out: list[dict] = []
    for p in snap.get("productos") or []:
        serie = p.get("series") or []
        for campo, etiqueta, fmt in (
            ("precio_kg", "precio", lambda v: f"precio {_soles(v)}/kg"),
            ("masa_hoy", "volumen", lambda v: f"ingreso {_toneladas(v)}"),
        ):
            pares = [(pt["fecha"], pt[campo]) for pt in serie
                     if pt.get("fecha") and pt.get(campo) is not None]
            # Se necesita historia previa suficiente para que el z tenga sentido.
            for i in range(6, len(pares)):
                fecha, valor = pares[i]
                hist = [v for _, v in pares[max(0, i - ventana):i]]
                z = _robust_z(hist, valor)
                if z is None or abs(z) < z_umbral:
                    continue
                out.append({
                    "slug": p["slug"], "nombre": p["nombre"], "tipo": etiqueta,
                    "fecha": fecha, "z": round(z, 2), "detalle": fmt(valor),
                })
    out.sort(key=lambda a: (a["fecha"], a["slug"], a["tipo"]))
    return out


def anomalias_destacadas(
    anomalias: list[dict], fecha_ultima: str | None,
    top_por_producto: int = TOP_ANOMALIAS_POR_PRODUCTO,
) -> list[dict]:
    """Selecciona qué anomalías merecen su PROPIO chunk recuperable.

    POR QUÉ NO TODAS. El barrido histórico devuelve ~4.900 anomalías sobre ~74.700
    observaciones (6,5%). No es un bug: la MAD de un precio mayorista es diminuta
    —el mismo precio se repite días seguidos— así que cualquier movimiento real
    supera 3,5 desviaciones robustas. El detector del dashboard tiene exactamente
    la misma propiedad; no la cambiamos aquí porque eso alteraría lo que el sitio
    muestra, y no es alcance de esta capa.

    Pero darle un embedding propio a cada una llenaría el top-k de eventos casi
    idénticos: el mismo modo de falla que evitamos al no usar grano diario. Así
    que se emite chunk propio solo para (a) las más extremas de cada producto y
    tipo, que es lo que alguien busca al preguntar "¿cuándo hubo saltos raros?",
    y (b) todas las del último día, para que el corpus coincida con lo que el
    dashboard llama "hoy".

    Ninguna información se pierde: las ~4.900 siguen apareciendo dentro del chunk
    del periodo en que cayeron. Lo que se acota es cuántas son recuperables por
    sí solas.
    """
    porgrupo: dict[tuple[str, str], list[dict]] = {}
    for a in anomalias:
        porgrupo.setdefault((a["slug"], a["tipo"]), []).append(a)

    elegidas: dict[str, dict] = {}
    for grupo in porgrupo.values():
        for a in sorted(grupo, key=lambda x: abs(x["z"]), reverse=True)[:top_por_producto]:
            elegidas[f"{a['slug']}:{a['fecha']}:{a['tipo']}"] = a
    if fecha_ultima:
        for a in anomalias:
            if a["fecha"] == fecha_ultima:
                elegidas[f"{a['slug']}:{a['fecha']}:{a['tipo']}"] = a
    return sorted(elegidas.values(), key=lambda a: (a["fecha"], a["slug"], a["tipo"]))


def _promedio_por_mes(serie: list[dict]) -> dict[int, float]:
    """{mes 1..12: promedio de precio_kg} sobre toda la historia del producto."""
    acc: dict[int, list[float]] = {}
    for pt in serie:
        v = pt.get("precio_kg")
        if v is None:
            continue
        mes = int(pt["fecha"][5:7])
        acc.setdefault(mes, []).append(float(v))
    return {m: sum(vs) / len(vs) for m, vs in sorted(acc.items())}


def _agrupa_por_periodo(serie: list[dict], granularidad: str
                        ) -> dict[str, list[dict]]:
    """{clave_periodo: [puntos...]} preservando el orden temporal."""
    out: dict[str, list[dict]] = {}
    for pt in serie:
        f = pt.get("fecha")
        if not f:
            continue
        out.setdefault(periodo_key(f, granularidad), []).append(pt)
    return out


# --------------------------------------------------------------------------- #
# Generadores de texto — uno por tipo de chunk
# --------------------------------------------------------------------------- #
def _texto_producto_periodo(
    nombre: str, unidad: str | None, mercado: str, etiqueta: str,
    puntos: list[dict], anomalias_periodo: list[dict],
    comovimientos: list[tuple[str, float]],
) -> str:
    """Párrafo de un producto en una ventana. El chunk de trabajo del corpus."""
    precios = [(p["fecha"], p["precio_kg"]) for p in puntos
               if p.get("precio_kg") is not None]
    masas = [p["masa_hoy"] for p in puntos if p.get("masa_hoy") is not None]

    lineas = [f"{nombre} · {etiqueta} · {mercado}."]

    if precios:
        f_ini, p_ini = precios[0]
        f_fin, p_fin = precios[-1]
        vals = [v for _, v in precios]
        lo, hi = min(vals), max(vals)
        prom = sum(vals) / len(vals)
        if len(precios) == 1:
            lineas.append(f"Precio: {_soles(p_fin)}/kg.")
        else:
            var = (100.0 * (p_fin - p_ini) / p_ini) if p_ini else None
            cambio = f" ({_pct(var)} en el periodo)" if var is not None else ""
            lineas.append(
                f"Precio: abrió en {_soles(p_ini)}/kg el {_fecha_corta(f_ini)} y "
                f"cerró en {_soles(p_fin)}/kg el {_fecha_corta(f_fin)}{cambio}.")
            lineas.append(
                f"Rango {_soles(lo)} – {_soles(hi)} por kg, promedio {_soles(prom)}.")
    else:
        lineas.append("Precio: sin dato publicado en el periodo.")

    if masas:
        total = sum(masas)
        lineas.append(
            f"Ingreso: {_toneladas(total)} en el periodo sobre "
            f"{len(masas)} día(s) con dato, promedio diario "
            f"{_toneladas(total / len(masas))}.")

    tendencias = [p.get("tendencia") for p in puntos if p.get("tendencia")]
    if tendencias:
        conteo: dict[str, int] = {}
        for t in tendencias:
            conteo[t] = conteo.get(t, 0) + 1
        detalle = ", ".join(
            f"{t} ({n} día{'s' if n > 1 else ''})"
            for t, n in sorted(conteo.items(), key=lambda kv: -kv[1]))
        lineas.append(f"Tendencia reportada por la fuente: {detalle}.")

    lineas.extend(
        f"Anomalía detectada: el {a.get('tipo')} se desvió de su historia "
        f"reciente el {a.get('fecha')} (z={a.get('z')}; {a.get('detalle')})."
        for a in anomalias_periodo)

    if comovimientos:
        detalle = ", ".join(f"{n} ({_pct(v)})" for n, v in comovimientos)
        lineas.append(
            f"Se movieron en el mismo sentido en el periodo: {detalle}. "
            f"Es co-movimiento observado, no una relación causal.")

    if unidad:
        lineas.append(f"Unidad de reporte de la fuente: {unidad}.")

    return "\n".join(lineas)


def _texto_producto_perfil(
    p: dict, correladas: list[tuple[str, float, int]], nombres: dict[str, str],
    mercado: str,
) -> str:
    """Ficha del producto: rango histórico, estacionalidad, pronóstico, correlación."""
    nombre = p["nombre"]
    serie = p.get("series") or []
    vals = [pt["precio_kg"] for pt in serie if pt.get("precio_kg") is not None]
    fechas = [pt["fecha"] for pt in serie if pt.get("fecha")]

    lineas = [f"Ficha de {nombre} ({p['slug']}) · {mercado}."]

    if p.get("unidad"):
        equiv = p.get("equiv_kg")
        eq = f", equivalente a {equiv} kg" if equiv else ""
        lineas.append(f"Se reporta por {p['unidad']}{eq}; los precios se "
                      f"normalizan a soles por kilo.")
    # `categoria` es NULL en todo el catálogo hoy: se omite en vez de inventarla.
    if p.get("categoria"):
        lineas.append(f"Categoría: {p['categoria']}.")

    if fechas and vals:
        lineas.append(
            f"Historia: {len(fechas)} días con dato entre {fechas[0]} y {fechas[-1]}.")
        lineas.append(
            f"Precio histórico: mínimo {_soles(min(vals))}, máximo "
            f"{_soles(max(vals))}, promedio {_soles(sum(vals) / len(vals))} por kg.")

    lat = p.get("latest") or {}
    if lat.get("precio_kg") is not None:
        lineas.append(
            f"Último dato ({lat.get('fecha')}): {_soles(lat['precio_kg'])}/kg, "
            f"{_pct(lat.get('var_pct'))} respecto al día anterior.")

    pormes = _promedio_por_mes(serie)
    if len(pormes) >= 6:
        detalle = " · ".join(f"{_MESES[m - 1]} {_soles(v)}" for m, v in pormes.items())
        lineas.append(f"Estacionalidad, promedio histórico por mes: {detalle}.")

    fc = p.get("forecast") or {}
    if fc.get("precio_estimado") is not None:
        lo, hi = (fc.get("intervalo") or [None, None])[:2]
        rango = f" (intervalo {_soles(lo)} – {_soles(hi)})" if lo is not None else ""
        linea = (f"Pronóstico vigente para el próximo día hábil: "
                 f"{_soles(fc['precio_estimado'])}/kg{rango}, método {fc.get('metodo')}.")
        mm, mb = fc.get("mae_modelo"), fc.get("mae_baseline")
        if mm is not None and mb is not None:
            veredicto = ("el modelo le gana al baseline" if mm < mb
                         else "aquí el modelo NO le gana al baseline")
            linea += f" MAE {mm:.3f} contra baseline {mb:.3f}: {veredicto}."
        lineas.append(linea + " Es una estimación beta, no una cifra del reporte.")

    if correladas:
        detalle = ", ".join(
            f"{nombres.get(s, s)} (r={r:+.2f}, {n} días)" for s, r, n in correladas)
        lineas.append(
            f"Productos cuyo precio se mueve junto al de {nombre}, por correlación "
            f"de retornos diarios: {detalle}. Correlación no implica causalidad.")

    return "\n".join(lineas)


def _texto_evento_anomalia(a: dict, contexto: str | None, mercado: str) -> str:
    lineas = [
        f"Anomalía de {a.get('tipo')} · {a.get('nombre')} · {_fecha_larga(a.get('fecha', ''))} · {mercado}.",
        f"El valor observado ({a.get('detalle')}) se apartó {abs(float(a.get('z', 0))):.1f} "
        f"desviaciones robustas de la historia reciente del producto.",
    ]
    if contexto:
        lineas.append(contexto)
    lineas.append(
        "Detección por z-score robusto sobre mediana y MAD (ventana de 30 datos, "
        "umbral 3.5). Es una señal estadística de que algo se salió de lo normal, "
        "no una explicación de por qué ocurrió: el dato no dice la causa.")
    return "\n".join(lineas)


def _texto_otro_mercado(prod: dict, nombre_mercado: str,
                        codigo: str | None, fecha: str) -> str:
    """Un producto de un mercado distinto del GMML, en su último día publicado.

    El aviso final NO es decorativo. Estos precios conviven en el mismo índice
    que los del GMML y un modelo que los mezcle produciría comparaciones falsas:
    el pollo vivo se mide por kilo vivo en pie, no por kilo de producto en un
    puesto mayorista de hortalizas. Decirlo en el propio chunk es más fiable que
    confiar en que el prompt del sistema lo recuerde en cada respuesta.

    Sin serie: `export._add_mercados` solo publica el último día de cada mercado,
    así que aquí no hay histórico que ofrecer y no se finge que lo haya.
    """
    nombre = prod.get("nombre") or prod.get("slug")
    precio = prod.get("precio_kg")
    var = prod.get("var_pct")

    partes = [f"{nombre} ({prod.get('slug')}) · {nombre_mercado}"
              + (f" [{codigo}]" if codigo else "") + f" · {_fecha_larga(fecha)}."]
    if precio is not None:
        partes.append(f"Precio: {_soles(precio)} por kilogramo.")
    else:
        partes.append("Sin precio publicado ese día.")
    if var is not None:
        # `_pct` antepone el signo, así que combinarlo con el verbo producía
        # "Bajó +8.2%". Se usa el valor con su signo y sin verbo redundante.
        partes.append(f"Variación respecto al día anterior: {_pct(var)}.")
    partes.append(
        f"{nombre_mercado} es un mercado DISTINTO del Gran Mercado Mayorista de "
        f"Lima: sus precios no son directamente comparables con los de las "
        f"hortalizas del GMML, y solo se publica el último día disponible.")
    return " ".join(partes)


def _texto_mercado_dia(
    fecha: str, mercado: str, filas: list[dict], anomalias_dia: list[dict],
) -> str:
    con_precio = [f for f in filas if f["precio"] is not None]
    con_var = [f for f in filas if f["var"] is not None]
    masas = [f["masa"] for f in filas if f["masa"] is not None]

    lineas = [f"{mercado} · {_fecha_larga(fecha)}.",
              f"{len(con_precio)} productos con precio publicado."]
    if masas:
        lineas.append(f"Ingreso total del día: {_toneladas(sum(masas))}.")

    # Extremos de precio del día. "¿qué está más barato hoy?" es de las preguntas
    # más frecuentes y es AGREGADA, no de un producto: sin esto no habría ningún
    # chunk que la contenga y la recuperación devolvería lo que se le pareciera.
    if con_precio:
        baratos = sorted(con_precio, key=lambda f: f["precio"])[:5]
        caros = sorted(con_precio, key=lambda f: -f["precio"])[:5]
        lineas.append("Lo más barato del día: " + ", ".join(
            f"{f['nombre']} ({_soles(f['precio'])}/kg)" for f in baratos) + ".")
        lineas.append("Lo más caro del día: " + ", ".join(
            f"{f['nombre']} ({_soles(f['precio'])}/kg)" for f in caros) + ".")

    if con_var:
        subas = sorted(con_var, key=lambda f: -f["var"])[:5]
        bajas = sorted(con_var, key=lambda f: f["var"])[:5]
        subas = [f for f in subas if f["var"] > 0]
        bajas = [f for f in bajas if f["var"] < 0]
        if subas:
            lineas.append("Mayores alzas: " + ", ".join(
                f"{f['nombre']} ({_pct(f['var'])} a {_soles(f['precio'])}/kg)"
                for f in subas) + ".")
        if bajas:
            lineas.append("Mayores bajas: " + ", ".join(
                f"{f['nombre']} ({_pct(f['var'])} a {_soles(f['precio'])}/kg)"
                for f in bajas) + ".")
        if not subas and not bajas:
            lineas.append("Sin variaciones de precio respecto al día anterior.")

    if anomalias_dia:
        detalle = ", ".join(f"{a.get('nombre')} ({a.get('tipo')}, z={a.get('z')})"
                            for a in anomalias_dia)
        lineas.append(f"Anomalías estadísticas detectadas ese día: {detalle}.")

    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# Construcción del corpus
# --------------------------------------------------------------------------- #
def _anomalias_por_slug(anomalias: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in anomalias:
        out.setdefault(a.get("slug"), []).append(a)
    return out


def _comovimientos(
    variaciones: dict[str, dict[str, float]], slug: str, periodo: str,
    nombres: dict[str, str], top: int = TOP_COMOVIMIENTOS,
) -> list[tuple[str, float]]:
    """Otros productos que se movieron en el MISMO sentido en ese periodo.

    Hecho observado sobre la ventana, no inferencia. Sirve para que
    "¿por qué subió la papa esta semana?" recupere qué más subió con ella.
    """
    propia = variaciones.get(slug, {}).get(periodo)
    if propia is None or propia == 0:
        return []
    signo = 1 if propia > 0 else -1
    otros = [
        (nombres.get(s, s), v)
        for s, porper in variaciones.items()
        if s != slug and (v := porper.get(periodo)) is not None
        and v != 0 and (1 if v > 0 else -1) == signo
    ]
    otros.sort(key=lambda t: abs(t[1]), reverse=True)
    return otros[:top]


def build_corpus(
    origen: Any,
    granularidad: str = GRANULARIDAD_DEFAULT,
    incluir: Iterable[str] | None = None,
) -> list[Chunk]:
    """Construye el corpus completo desde un snapshot.

    `granularidad`: dia|semana|mes — solo afecta a los chunks producto-periodo.
    `incluir`: subconjunto de tipos de chunk (por defecto, todos).

    Determinista: mismos hechos -> mismos ids y mismos textos.
    """
    if granularidad not in GRANULARIDADES:
        raise ValueError(f"granularidad debe ser una de {GRANULARIDADES}")
    tipos = set(incluir) if incluir is not None else {
        TIPO_PRODUCTO_PERIODO, TIPO_PRODUCTO_PERFIL,
        TIPO_EVENTO_ANOMALIA, TIPO_MERCADO_DIA, TIPO_OTRO_MERCADO,
    }

    snap = cargar_snapshot(origen)
    mercado = snap.get("mercado") or "Gran Mercado Mayorista de Lima (GMML)"
    productos = snap.get("productos") or []
    nombres = {p["slug"]: p["nombre"] for p in productos}
    # Barrido histórico, no `snapshot.anomalias` (que solo trae el último día).
    # Ver `anomalias_historicas`: mismo estadístico, toda la serie.
    anomalias = anomalias_historicas(snap)
    anom_por_slug = _anomalias_por_slug(anomalias)

    chunks: list[Chunk] = []

    # --- Variaciones por periodo: se calculan una vez y las usan dos generadores.
    variaciones: dict[str, dict[str, float]] = {}
    grupos_por_slug: dict[str, dict[str, list[dict]]] = {}
    for p in productos:
        grupos = _agrupa_por_periodo(p.get("series") or [], granularidad)
        grupos_por_slug[p["slug"]] = grupos
        porper: dict[str, float] = {}
        for key, puntos in grupos.items():
            precios = [pt["precio_kg"] for pt in puntos
                       if pt.get("precio_kg") is not None]
            if len(precios) >= 2 and precios[0]:
                porper[key] = 100.0 * (precios[-1] - precios[0]) / precios[0]
        variaciones[p["slug"]] = porper

    # --- producto-periodo -------------------------------------------------
    if TIPO_PRODUCTO_PERIODO in tipos:
        for p in productos:
            slug = p["slug"]
            anoms = anom_por_slug.get(slug, [])
            for key, puntos in sorted(grupos_por_slug[slug].items()):
                fechas = [pt["fecha"] for pt in puntos if pt.get("fecha")]
                if not fechas:
                    continue
                ini, fin = fechas[0], fechas[-1]
                del_periodo = [a for a in anoms
                               if a.get("fecha") and ini <= a["fecha"] <= fin]
                texto = _texto_producto_periodo(
                    p["nombre"], p.get("unidad"), mercado,
                    periodo_label(key, granularidad, ini, fin),
                    puntos, del_periodo,
                    _comovimientos(variaciones, slug, key, nombres),
                )
                chunks.append(Chunk(
                    id=f"{TIPO_PRODUCTO_PERIODO}:{slug}:{key}",
                    tipo=TIPO_PRODUCTO_PERIODO, texto=texto,
                    slug=slug, producto=p["nombre"], mercado=mercado,
                    fecha_inicio=ini, fecha_fin=fin,
                ))

    # --- producto-perfil --------------------------------------------------
    if TIPO_PRODUCTO_PERFIL in tipos:
        corr = correlaciones(productos)
        for p in productos:
            serie = p.get("series") or []
            fechas = [pt["fecha"] for pt in serie if pt.get("fecha")]
            chunks.append(Chunk(
                id=f"{TIPO_PRODUCTO_PERFIL}:{p['slug']}",
                tipo=TIPO_PRODUCTO_PERFIL,
                texto=_texto_producto_perfil(p, corr.get(p["slug"], []), nombres, mercado),
                slug=p["slug"], producto=p["nombre"], mercado=mercado,
                fecha_inicio=fechas[0] if fechas else "",
                fecha_fin=fechas[-1] if fechas else "",
            ))

    # --- evento-anomalia --------------------------------------------------
    if TIPO_EVENTO_ANOMALIA in tipos:
        for a in anomalias_destacadas(anomalias, snap.get("latestFecha")):
            slug, fecha = a.get("slug"), a.get("fecha")
            if not fecha:
                continue
            # Contexto: la ventana del producto donde cayó la anomalía.
            contexto = None
            grupos = grupos_por_slug.get(slug) or {}
            key = periodo_key(fecha, granularidad)
            puntos = grupos.get(key)
            if puntos:
                precios = [pt["precio_kg"] for pt in puntos
                           if pt.get("precio_kg") is not None]
                if len(precios) >= 2:
                    contexto = (
                        f"En el mismo periodo el precio fue de {_soles(precios[0])} "
                        f"a {_soles(precios[-1])} por kg.")
            chunks.append(Chunk(
                id=f"{TIPO_EVENTO_ANOMALIA}:{slug}:{fecha}:{a.get('tipo')}",
                tipo=TIPO_EVENTO_ANOMALIA,
                texto=_texto_evento_anomalia(a, contexto, mercado),
                slug=slug, producto=a.get("nombre"), mercado=mercado,
                fecha_inicio=fecha, fecha_fin=fecha,
            ))

    # --- otro-mercado-dia -------------------------------------------------
    # POR QUÉ EXISTE ESTE BLOQUE
    # El snapshot trae `mercados` desde que se ingesta SISAP, el dashboard lo
    # muestra, y sin embargo el corpus no lo miraba. Consecuencia medida en
    # producción: a "¿cómo va el pollo vivo?" el asistente respondía "no figura
    # entre los productos que seguimos" — con el dato delante, en el mismo JSON.
    #
    # Eso es peor que un "no sé": es una negación afirmada como hecho. Y era un
    # empeoramiento causado por mejorar: el peldaño de catálogo SÍ incluía estos
    # productos, pero el RAG gana y nunca le cede el turno.
    if TIPO_OTRO_MERCADO in tipos:
        for m in snap.get("mercados") or []:
            fecha = m.get("latestFecha")
            nombre_mercado = m.get("nombre") or m.get("codigo") or "otro mercado"
            if not fecha:
                continue
            for prod in m.get("productos") or []:
                slug = prod.get("slug")
                if not slug:
                    continue
                chunks.append(Chunk(
                    id=f"{TIPO_OTRO_MERCADO}:{m.get('codigo')}:{slug}:{fecha}",
                    tipo=TIPO_OTRO_MERCADO,
                    texto=_texto_otro_mercado(prod, nombre_mercado,
                                              m.get("codigo"), fecha),
                    slug=slug, producto=prod.get("nombre"),
                    mercado=nombre_mercado,
                    fecha_inicio=fecha, fecha_fin=fecha,
                ))

    # --- mercado-dia ------------------------------------------------------
    if TIPO_MERCADO_DIA in tipos:
        por_dia: dict[str, list[dict]] = {}
        for p in productos:
            previo: float | None = None
            for pt in p.get("series") or []:
                precio = pt.get("precio_kg")
                var = None
                if precio is not None and previo is not None and previo != 0:
                    var = 100.0 * (precio - previo) / previo
                if precio is not None:
                    previo = precio
                por_dia.setdefault(pt["fecha"], []).append({
                    "nombre": p["nombre"], "precio": precio,
                    "var": var, "masa": pt.get("masa_hoy"),
                })
        anom_por_fecha: dict[str, list[dict]] = {}
        for a in anomalias:
            if a.get("fecha"):
                anom_por_fecha.setdefault(a["fecha"], []).append(a)
        for fecha, filas in sorted(por_dia.items()):
            chunks.append(Chunk(
                id=f"{TIPO_MERCADO_DIA}:{fecha}",
                tipo=TIPO_MERCADO_DIA,
                texto=_texto_mercado_dia(fecha, mercado, filas,
                                         anom_por_fecha.get(fecha, [])),
                slug=None, producto=None, mercado=mercado,
                fecha_inicio=fecha, fecha_fin=fecha,
            ))

    chunks.sort(key=lambda c: c.id)
    return chunks


# --------------------------------------------------------------------------- #
# Demostración: tamaños del corpus por granularidad (los números del README)
# --------------------------------------------------------------------------- #
def main() -> None:
    import os
    import sys

    ruta = os.environ.get("PRECIOVIVO_EXPORT", "../web/data/snapshot.json")
    if len(sys.argv) > 1:
        ruta = sys.argv[1]
    snap = cargar_snapshot(ruta)
    print(f"== Corpus Precio Vivo · fuente {ruta} · latest {snap.get('latestFecha')} ==\n")
    for gran in GRANULARIDADES:
        chunks = build_corpus(snap, granularidad=gran)
        porc: dict[str, int] = {}
        for c in chunks:
            porc[c.tipo] = porc.get(c.tipo, 0) + 1
        chars = sum(len(c.texto) for c in chunks)
        detalle = " · ".join(f"{k}={v}" for k, v in sorted(porc.items()))
        print(f"granularidad={gran:<7} total={len(chunks):>6}  {detalle}")
        print(f"{'':<20} texto={chars/1_000_000:.2f} MB  "
              f"media={chars // max(len(chunks), 1)} chars/chunk\n")

    print("--- muestra (granularidad=semana) ---")
    ejemplo = build_corpus(snap, granularidad="semana")
    for tipo in (TIPO_PRODUCTO_PERIODO, TIPO_PRODUCTO_PERFIL,
                 TIPO_EVENTO_ANOMALIA, TIPO_MERCADO_DIA, TIPO_OTRO_MERCADO):
        c = next((x for x in ejemplo if x.tipo == tipo), None)
        if c:
            print(f"\n[{c.id}]\n{c.texto}")


if __name__ == "__main__":
    main()
