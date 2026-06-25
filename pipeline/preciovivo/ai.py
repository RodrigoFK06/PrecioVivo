"""Capa IA (Fase 4) de Precio Vivo: narración y consultas en lenguaje natural.

Principio rector: la IA NO inventa números. Recibe HECHOS ya calculados aguas
arriba (movers del día, ingreso/volumen, anomalías) y solo los redacta en prosa
en español. Toda función puede operar SIN clave de API: si no hay AI_API_KEY
(proveedor OpenAI-compatible, DeepSeek por defecto; o la llamada falla), se
devuelve una versión DETERMINISTA en Python con fuente='fallback'. Funciona
perfecto sin clave.

La detección de anomalías (`detecta_anomalias`) es 100% estadística (z-score
robusto sobre MAD); no usa el LLM y es la fuente de hechos para
`narrate_anomalies`.

API pública (funciones puras):
  - daily_summary(facts)        -> {"texto": str, "fuente": "llm"|"fallback"}
  - narrate_anomalies(anomalias)-> {"texto": str, "fuente": "llm"|"fallback"}
  - nl_query(pregunta, esquema_desc) -> {"sql": str, "fuente": "llm"|"fallback"}
  - detecta_anomalias(origen)   -> list[dict]  (contrato snapshot.anomalias)
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import statistics
from typing import Any

try:  # Carga .env (clave DeepSeek, etc.) si existe — opcional, no-op sin el paquete.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# Proveedor LLM agnóstico (OpenAI-compatible). Por defecto DeepSeek (barato).
# Config por entorno (sin clave => modo fallback determinista):
#   AI_API_KEY  (o DEEPSEEK_API_KEY)
#   AI_BASE_URL (default https://api.deepseek.com)
#   AI_MODEL    (default deepseek-chat)
# Local/gratis (Ollama): AI_BASE_URL=http://localhost:11434/v1, AI_MODEL=llama3.1
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.deepseek.com")
AI_MODEL = os.environ.get("AI_MODEL", "deepseek-chat")
# Ventana de historia reciente para el z-score robusto y umbral de desviación.
ANOMALIA_VENTANA = 30
ANOMALIA_Z = 3.5
# Factor que vuelve la MAD un estimador consistente de la desviación estándar.
_MAD_K = 1.4826
# z finito grande para baselines de dispersión cero (evita propagar infinito).
_Z_SATURADO = 99.0


def _api_key() -> str | None:
    return os.environ.get("AI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")


# --------------------------------------------------------------------------- #
# Cliente LLM (OpenAI-compatible, perezoso) con fallback obligatorio
# --------------------------------------------------------------------------- #
def _client():
    """Cliente OpenAI-compatible (DeepSeek por defecto), o None sin clave/SDK.

    Nunca lanza: la ausencia de clave es el camino normal (modo fallback).
    """
    key = _api_key()
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key, base_url=AI_BASE_URL)
    except Exception:
        return None


def _ask_llm(system: str, prompt: str, max_tokens: int = 1024) -> str | None:
    """Una llamada de chat (OpenAI-compatible). Devuelve el texto, o None si falla.

    Cualquier excepción (red, autenticación, rate-limit) se traga y degrada al
    fallback determinista.
    """
    client = _client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=AI_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        texto = (resp.choices[0].message.content or "").strip()
        return texto or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Utilidades de formato (compartidas por LLM y fallback)
# --------------------------------------------------------------------------- #
def _fmt_soles(x: Any) -> str:
    try:
        return f"S/ {float(x):.2f}"
    except (TypeError, ValueError):
        return "S/ —"


def _fmt_pct(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    signo = "+" if v > 0 else ""
    return f"{signo}{v:.1f}%"


def _fmt_t(x: Any) -> str:
    try:
        return f"{float(x):.0f} t"
    except (TypeError, ValueError):
        return "— t"


# --------------------------------------------------------------------------- #
# 1) Resumen del día
# --------------------------------------------------------------------------- #
def daily_summary(facts: dict) -> dict:
    """Redacta un resumen del día a partir de hechos ya calculados.

    `facts` (todos los campos son opcionales; el fallback tolera ausencias):
      - fecha: str
      - mercado: str
      - subas:  list[{nombre, var_pct, precio_kg}]   (mayores alzas)
      - bajas:  list[{nombre, var_pct, precio_kg}]   (mayores caídas)
      - ingreso_total_t: float        (volumen/masa total del día, en toneladas)
      - ingreso_var_pct: float        (variación del volumen vs. referencia)
      - n_productos: int

    Devuelve {"texto", "fuente"}. La IA solo narra estos números; no produce
    cifras nuevas.
    """
    fallback = _daily_summary_fallback(facts)
    system = (
        "Eres el analista de Precio Vivo, inteligencia de precios mayoristas del "
        "Gran Mercado Mayorista de Lima (GMML). Redactas en español peruano, claro "
        "y sobrio, sin emojis. REGLA ABSOLUTA: usa EXCLUSIVAMENTE los números que "
        "aparecen en los datos entregados; está terminantemente prohibido inventar, "
        "estimar o redondear a cifras distintas. No predigas precios. Devuelve 2 a 4 "
        "frases, sin viñetas ni encabezados."
    )
    prompt = (
        "Redacta el resumen del día con estos hechos (JSON). Menciona los "
        "principales movimientos de precio y el volumen de ingreso si está "
        "presente. No agregues cifras que no estén aquí.\n\n"
        f"{_compact_json(facts)}"
    )
    texto = _ask_llm(system, prompt, max_tokens=800)
    if texto:
        return {"texto": texto, "fuente": "llm"}
    return {"texto": fallback, "fuente": "fallback"}


def _daily_summary_fallback(facts: dict) -> str:
    fecha = facts.get("fecha")
    mercado = facts.get("mercado") or "el GMML"
    n = facts.get("n_productos")
    subas = facts.get("subas") or []
    bajas = facts.get("bajas") or []

    partes: list[str] = []
    cab = f"Resumen del {fecha} en {mercado}." if fecha else f"Resumen de precios en {mercado}."
    if isinstance(n, int):
        cab = cab[:-1] + f", sobre {n} productos seguidos."
    partes.append(cab)

    if subas:
        top = subas[0]
        det = ", ".join(
            f"{s.get('nombre')} ({_fmt_pct(s.get('var_pct'))})" for s in subas[:3]
        )
        partes.append(
            f"Mayores alzas: {det}; encabeza {top.get('nombre')} a "
            f"{_fmt_soles(top.get('precio_kg'))}/kg."
        )
    if bajas:
        top = bajas[0]
        det = ", ".join(
            f"{b.get('nombre')} ({_fmt_pct(b.get('var_pct'))})" for b in bajas[:3]
        )
        partes.append(
            f"Mayores caídas: {det}; destaca {top.get('nombre')} a "
            f"{_fmt_soles(top.get('precio_kg'))}/kg."
        )
    if not subas and not bajas:
        partes.append("Sin variaciones de precio destacables en la jornada.")

    ingreso = facts.get("ingreso_total_t")
    if ingreso is not None:
        frase = f"Ingreso total estimado: {_fmt_t(ingreso)}"
        var = facts.get("ingreso_var_pct")
        if var is not None:
            frase += f" ({_fmt_pct(var)} respecto a su referencia reciente)"
        partes.append(frase + ".")

    return " ".join(partes)


# --------------------------------------------------------------------------- #
# 2) Narración de anomalías
# --------------------------------------------------------------------------- #
def narrate_anomalies(anomalias: list) -> dict:
    """Redacta en prosa una lista de anomalías ya detectadas estadísticamente.

    Cada anomalía sigue el contrato snapshot.anomalias:
      {slug, nombre, tipo:'precio'|'volumen', fecha, z, detalle}

    Devuelve {"texto", "fuente"}. La IA solo describe; los z-scores y cifras
    provienen de `detecta_anomalias`, no del modelo.
    """
    fallback = _narrate_anomalies_fallback(anomalias)
    if not anomalias:
        return {"texto": fallback, "fuente": "fallback"}

    system = (
        "Eres el analista de Precio Vivo (precios mayoristas del GMML, Lima). "
        "Describes en español peruano, claro y sobrio, sin emojis, las anomalías "
        "estadísticas que te entregan. REGLA ABSOLUTA: usa solo los datos dados; "
        "no inventes números ni causas; no especules sobre el motivo (no hay datos "
        "para ello). No predigas. Una o dos frases por anomalía, máximo un párrafo."
    )
    prompt = (
        "Describe estas anomalías detectadas por z-score robusto. Indica producto, "
        "tipo (precio o volumen), fecha y magnitud (z). No agregues explicaciones "
        "causales.\n\n"
        f"{_compact_json(anomalias)}"
    )
    texto = _ask_llm(system, prompt, max_tokens=900)
    if texto:
        return {"texto": texto, "fuente": "llm"}
    return {"texto": fallback, "fuente": "fallback"}


def _narrate_anomalies_fallback(anomalias: list) -> str:
    if not anomalias:
        return "No se detectaron anomalías estadísticas en el periodo analizado."

    n = len(anomalias)
    cab = (
        f"Se detectó 1 anomalía estadística."
        if n == 1
        else f"Se detectaron {n} anomalías estadísticas."
    )
    frases = [cab]
    for a in anomalias[:8]:
        tipo = "el precio" if a.get("tipo") == "precio" else "el volumen"
        try:
            z = float(a.get("z"))
            zmag = f"z={z:+.1f}"
        except (TypeError, ValueError):
            zmag = "z=—"
        det = a.get("detalle")
        base = f"{a.get('nombre')}: {tipo} se desvió de su historia reciente ({zmag}) el {a.get('fecha')}"
        frases.append(base + (f"; {det}." if det else "."))
    if n > 8:
        frases.append(f"(+{n - 8} adicionales no listadas).")
    return " ".join(frases)


# --------------------------------------------------------------------------- #
# 3) Consulta en lenguaje natural -> SQL SELECT de solo lectura
# --------------------------------------------------------------------------- #
# Solo se permite UNA sentencia SELECT (o WITH ... SELECT). Cualquier palabra
# de escritura/DDL/PRAGMA hace que se rechace y se caiga al fallback.
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|pragma|vacuum|reindex|grant|revoke|"
    r"begin|commit|rollback|savepoint"
    r")\b",
    re.IGNORECASE,
)

ESQUEMA_DESC_DEFAULT = (
    "Tablas (SQLite):\n"
    "  productos(id, nombre_canonico, categoria, unidad_default, equiv_kg)\n"
    "  precios_diarios(fecha TEXT 'YYYY-MM-DD', mercado_id, producto_id,\n"
    "    masa_hoy, masa_7d, unidad, equiv_kg,\n"
    "    precio_hoy_kg, precio_ayer_kg, precio_7d_kg, tendencia)\n"
    "  precios_diarios.producto_id = productos.id\n"
    "Precios en soles por kilo (precio_hoy_kg). Masa/ingreso en toneladas."
)


def is_safe_select(sql: str) -> bool:
    """True solo si `sql` es UNA consulta SELECT de solo lectura.

    Rechaza múltiples sentencias, escrituras, DDL, PRAGMA y comentarios que
    podrían ocultar otra sentencia.
    """
    if not sql or not sql.strip():
        return False
    s = sql.strip()
    # Sin comentarios (podrían esconder una segunda sentencia).
    if "--" in s or "/*" in s:
        return False
    # Una sola sentencia: a lo sumo un ';' final.
    cuerpo = s[:-1] if s.endswith(";") else s
    if ";" in cuerpo:
        return False
    low = cuerpo.lstrip().lower()
    if not (low.startswith("select") or low.startswith("with")):
        return False
    if _FORBIDDEN.search(cuerpo):
        return False
    return True


def nl_query(pregunta: str, esquema_desc: str | None = None) -> dict:
    """Traduce una pregunta en español a UNA consulta SQL SELECT de solo lectura.

    Devuelve {"sql", "fuente"}. La SQL del LLM se VALIDA con `is_safe_select`;
    si no pasa (o no hay clave), se devuelve un SELECT determinista de respaldo.
    Nunca devuelve una sentencia de escritura.
    """
    esquema = esquema_desc or ESQUEMA_DESC_DEFAULT
    fallback_sql = _nl_query_fallback(pregunta)

    system = (
        "Traduces preguntas en español sobre precios mayoristas a SQL para SQLite. "
        "Devuelves EXCLUSIVAMENTE una única sentencia SELECT de solo lectura, sin "
        "punto y coma final, sin comentarios, sin texto adicional, sin bloques de "
        "código. PROHIBIDO: INSERT, UPDATE, DELETE, DDL, PRAGMA o múltiples "
        "sentencias. Si la pregunta no se puede responder con un SELECT sobre el "
        "esquema, responde con la palabra NULL."
    )
    prompt = (
        f"Esquema:\n{esquema}\n\n"
        f"Pregunta: {pregunta}\n\n"
        "Devuelve solo la consulta SELECT."
    )
    texto = _ask_llm(system, prompt, max_tokens=600)
    if texto:
        sql = _strip_sql(texto)
        if is_safe_select(sql):
            return {"sql": sql, "fuente": "llm"}
    return {"sql": fallback_sql, "fuente": "fallback"}


def _strip_sql(texto: str) -> str:
    """Limpia la respuesta del modelo: quita cercos ```sql, prefijos y ';' final."""
    s = texto.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    # Si el modelo antepone "SQL:" u otra etiqueta, recórtala desde el primer SELECT/WITH.
    m = re.search(r"\b(select|with)\b", s, re.IGNORECASE)
    if m:
        s = s[m.start():]
    return s.strip().rstrip(";").strip()


def _nl_query_fallback(pregunta: str) -> str:
    """SELECT determinista de respaldo, derivado por palabras clave.

    No pretende responder cualquier pregunta: ofrece una consulta útil y SIEMPRE
    de solo lectura. Garantiza que el contrato (una SELECT válida) se cumple sin
    LLM.
    """
    q = (pregunta or "").lower()
    q = "".join(
        c for c in __import__("unicodedata").normalize("NFKD", q)
        if not __import__("unicodedata").combining(c)
    )

    if any(w in q for w in ("mas caro", "mas caros", "mayor precio", "mas alto")):
        return (
            "SELECT p.nombre_canonico, pd.precio_hoy_kg "
            "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            "WHERE pd.fecha = (SELECT MAX(fecha) FROM precios_diarios) "
            "AND pd.precio_hoy_kg IS NOT NULL "
            "ORDER BY pd.precio_hoy_kg DESC LIMIT 10"
        )
    if any(w in q for w in ("mas barato", "mas baratos", "menor precio", "mas bajo")):
        return (
            "SELECT p.nombre_canonico, pd.precio_hoy_kg "
            "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            "WHERE pd.fecha = (SELECT MAX(fecha) FROM precios_diarios) "
            "AND pd.precio_hoy_kg IS NOT NULL "
            "ORDER BY pd.precio_hoy_kg ASC LIMIT 10"
        )
    if any(w in q for w in ("ingreso", "volumen", "masa", "tonelada")):
        return (
            "SELECT p.nombre_canonico, pd.masa_hoy "
            "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            "WHERE pd.fecha = (SELECT MAX(fecha) FROM precios_diarios) "
            "AND pd.masa_hoy IS NOT NULL "
            "ORDER BY pd.masa_hoy DESC LIMIT 10"
        )
    if "alza" in q or "subio" in q or "subieron" in q:
        return (
            "SELECT p.nombre_canonico, pd.tendencia, pd.precio_hoy_kg "
            "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            "WHERE pd.fecha = (SELECT MAX(fecha) FROM precios_diarios) "
            "AND pd.tendencia IN ('En Alza','Baja Notable') "
            "ORDER BY p.nombre_canonico"
        )
    # Respaldo genérico: precios del último día.
    return (
        "SELECT p.nombre_canonico, pd.precio_hoy_kg, pd.tendencia "
        "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
        "WHERE pd.fecha = (SELECT MAX(fecha) FROM precios_diarios) "
        "ORDER BY p.nombre_canonico"
    )


# --------------------------------------------------------------------------- #
# 4) Detección de anomalías (estadística, sin LLM)
# --------------------------------------------------------------------------- #
def _robust_z(serie: list[float], valor: float) -> float | None:
    """z-score robusto de `valor` respecto a `serie` (mediana + MAD).

    Devuelve None si la serie es demasiado corta o si `valor` coincide con la
    historia (no hay desviación). Más robusto que el z clásico ante outliers
    (justamente lo que buscamos).

    Caso de dispersión cero: si la historia es (casi) constante pero `valor`
    se aparta de ella, es la anomalía MÁS clara posible. La MAD/desv. estándar
    valen 0 y el z clásico daría infinito; devolvemos un z grande y finito con
    el signo correcto para que cruce cualquier umbral razonable sin propagar inf.
    """
    if len(serie) < 5:
        return None
    med = statistics.median(serie)
    desv = [abs(x - med) for x in serie]
    mad = statistics.median(desv)
    if mad > 0:
        return (valor - med) / (_MAD_K * mad)
    # Sin MAD (serie ~constante): cae al desvío estándar como respaldo.
    try:
        sd = statistics.pstdev(serie)
    except statistics.StatisticsError:
        return None
    if sd > 0:
        return (valor - med) / sd
    # Dispersión exactamente cero (baseline constante). Si valor == baseline,
    # no hay anomalía; si difiere, márcala como anomalía fuerte (z saturado).
    if valor == med:
        return None
    return math.copysign(_Z_SATURADO, valor - med)


def _series_desde_db(db_path: str) -> dict:
    """Lee de SQLite las series por producto: precio_hoy_kg y masa_hoy ordenadas."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    q = (
        "SELECT p.nombre_canonico AS nombre, pd.fecha, "
        "       pd.precio_hoy_kg AS precio, pd.masa_hoy AS masa "
        "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
        "ORDER BY p.nombre_canonico, pd.fecha"
    )
    for r in c.execute(q):
        d = out.setdefault(r["nombre"], {"fechas": [], "precio": [], "masa": []})
        d["fechas"].append(r["fecha"])
        d["precio"].append(r["precio"])
        d["masa"].append(r["masa"])
    c.close()
    return out


def _series_desde_snapshot(snap: dict) -> dict:
    """Reconstruye las series por producto desde un snapshot.json ya cargado."""
    out: dict[str, dict] = {}
    for p in snap.get("productos", []):
        d = out.setdefault(p["nombre"], {"fechas": [], "precio": [], "masa": []})
        for pt in p.get("series", []):
            d["fechas"].append(pt.get("fecha"))
            d["precio"].append(pt.get("precio_kg"))
            d["masa"].append(pt.get("masa_hoy"))
    return out


def detecta_anomalias(
    origen: Any,
    ventana: int = ANOMALIA_VENTANA,
    z_umbral: float = ANOMALIA_Z,
) -> list:
    """Detecta anomalías del ÚLTIMO día por z-score robusto (sin LLM).

    `origen` puede ser:
      - una ruta a la base SQLite (str),
      - un dict snapshot.json ya cargado,
      - un dict de series ya armado {nombre: {fechas, precio, masa}}.

    Marca un producto si el precio o el volumen de su último día se desvía más de
    `z_umbral` desviaciones (robustas) de su historia reciente (últimos `ventana`
    puntos). Devuelve una lista con el contrato snapshot.anomalias:
      [{slug, nombre, tipo:'precio'|'volumen', fecha, z, detalle}]
    ordenada por |z| descendente.
    """
    from .export import slugify  # reutiliza el slug canónico del exportador

    if isinstance(origen, str):
        series = _series_desde_db(origen)
    elif isinstance(origen, dict) and "productos" in origen:
        series = _series_desde_snapshot(origen)
    elif isinstance(origen, dict):
        series = origen
    else:
        raise TypeError("origen debe ser ruta SQLite, snapshot dict o dict de series")

    anomalias: list[dict] = []
    for nombre, d in series.items():
        fechas = d.get("fechas") or []
        if not fechas:
            continue
        for tipo, etiqueta, fmt in (
            ("precio", "precio", lambda v: f"precio {_fmt_soles(v)}/kg"),
            ("masa", "volumen", lambda v: f"ingreso {_fmt_t(v)}"),
        ):
            vals = d.get(tipo) or []
            # Empareja valor con fecha y descarta huecos (None).
            pares = [(f, v) for f, v in zip(fechas, vals) if v is not None]
            if len(pares) < 6:
                continue
            ult_fecha, ult_val = pares[-1]
            hist = [v for _, v in pares[:-1]][-ventana:]
            z = _robust_z(hist, ult_val)
            if z is None or abs(z) < z_umbral:
                continue
            anomalias.append({
                "slug": slugify(nombre),
                "nombre": nombre,
                "tipo": etiqueta,
                "fecha": ult_fecha,
                "z": round(z, 2),
                "detalle": fmt(ult_val),
            })

    anomalias.sort(key=lambda a: abs(a["z"]), reverse=True)
    return anomalias


# --------------------------------------------------------------------------- #
# Helpers internos
# --------------------------------------------------------------------------- #
def _compact_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# Demostración: fallback (sin clave) + detección de anomalías sobre la BD local
# --------------------------------------------------------------------------- #
def _demo_facts(db_path: str) -> dict:
    """Arma 'facts' de muestra para el resumen del día desde la BD (solo demo)."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    fila = c.execute("SELECT MAX(fecha) AS f FROM precios_diarios").fetchone()
    fecha = fila["f"] if fila else None
    movers = []
    if fecha:
        q = (
            "SELECT p.nombre_canonico AS nombre, pd.precio_hoy_kg AS hoy, "
            "       pd.precio_ayer_kg AS ayer "
            "FROM precios_diarios pd JOIN productos p ON p.id = pd.producto_id "
            "WHERE pd.fecha = ? AND pd.precio_hoy_kg IS NOT NULL "
            "      AND pd.precio_ayer_kg IS NOT NULL AND pd.precio_ayer_kg <> 0"
        )
        for r in c.execute(q, (fecha,)):
            var = 100.0 * (r["hoy"] - r["ayer"]) / r["ayer"]
            movers.append({"nombre": r["nombre"], "var_pct": round(var, 1),
                           "precio_kg": r["hoy"]})
        ingreso = c.execute(
            "SELECT SUM(masa_hoy) AS t FROM precios_diarios WHERE fecha = ?", (fecha,)
        ).fetchone()["t"]
    else:
        ingreso = None
    n_prod = c.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
    c.close()

    movers.sort(key=lambda m: m["var_pct"])
    subas = list(reversed(movers[-3:])) if movers else []
    bajas = movers[:3] if movers else []
    return {
        "fecha": fecha,
        "mercado": "Gran Mercado Mayorista de Lima (GMML)",
        "n_productos": n_prod,
        "subas": [s for s in subas if s["var_pct"] > 0],
        "bajas": [b for b in bajas if b["var_pct"] < 0],
        "ingreso_total_t": round(ingreso, 1) if ingreso is not None else None,
    }


def main():
    db = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")
    tiene_clave = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"== Precio Vivo · capa IA · DB={db} · ANTHROPIC_API_KEY={'sí' if tiene_clave else 'no'} ==\n")

    facts = _demo_facts(db)

    print("--- daily_summary ---")
    r = daily_summary(facts)
    print(f"[fuente={r['fuente']}]\n{r['texto']}\n")

    print("--- detecta_anomalias (z-score robusto, sin LLM) ---")
    anoms = detecta_anomalias(db)
    print(f"detectadas: {len(anoms)} (umbral z={ANOMALIA_Z}, ventana={ANOMALIA_VENTANA})")
    for a in anoms[:8]:
        print(f"  {a['nombre']:<22.22} {a['tipo']:<8} z={a['z']:+6.2f} {a['fecha']}  {a['detalle']}")
    print()

    print("--- narrate_anomalies ---")
    r = narrate_anomalies(anoms)
    print(f"[fuente={r['fuente']}]\n{r['texto']}\n")

    print("--- nl_query ---")
    for preg in ("¿Cuáles son los 5 productos más caros hoy?",
                 "¿Qué productos están en alza?"):
        r = nl_query(preg)
        ok = is_safe_select(r["sql"])
        print(f"[fuente={r['fuente']} · SELECT seguro={ok}] {preg}\n  {r['sql']}\n")


if __name__ == "__main__":
    main()
