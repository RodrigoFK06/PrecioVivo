"""SISAP cross-check source (Fase 2, §4 redundancia).

MIDAGRI publishes a short, stable, WAF-free daily PDF with mayorista-Lima prices
already expressed in **S/ por Kg** for a handful of products across three markets
(GRAN MERCADO MAYORISTA DE LIMA, MERCADO MAYORISTA DE AVES VIVAS -> POLLO VIVO,
MERCADO MAYORISTA NRO 2-FRUTAS). We use it as an INDEPENDENT second opinion on the
GMML numbers we already ingest from collection 335, not as a primary feed.

Layout columns (left -> right):
  Mercado | Producto | Precio Hoy (S/xKg) | Precio Ayer | Diferencia |
  Promedio Mes | Promedio Últimos 7 días

As with the reporte-335, the flowed text order is not guaranteed and the numeric
band floats ~1px above the text baseline (so each logical row splits into two
`top` values). We therefore parse by COORDINATE: cluster rows with a y-tolerance
that re-merges that split, then assign the 5 numeric columns by x-center band and
recover the producto by stripping the known mercado prefix from the left text.

Public API:
  fetch_sisap()                 -> (local_path, sha256, nbytes)
  parse_sisap(pdf_path)         -> list[dict]   (one dict per producto row)
  cross_check(sisap_rows, db)   -> list[dict]   (GMML deltas vs ../data/preciovivo.db)
  build_check(cc)               -> dict          (resumen serializable del cross-check)
  save_check(cc, db) / load_check(db)            (cache JSON junto a la BD, como
                                                  forecast_cache.json; export lo adjunta
                                                  al snapshot sin tocar la red)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timezone

import pdfplumber
import requests

SISAP_URL = ("http://sistemas.midagri.gob.pe/sisap/intranet/mayoristas_lima/"
             "modulos/reporte_precios.pdf")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
RAW_DIR = os.environ.get("PRECIOVIVO_RAW", "../data/raw")
FUENTE = "sisap"
DEFAULT_DB = os.environ.get("PRECIOVIVO_DB", "../data/preciovivo.db")

# Cross-check threshold (§4): flag GMML prices that disagree by more than this.
DELTA_FLAG = 0.15

# The three mercado labels are a closed set; the longest-matching prefix wins so
# "MERCADO MAYORISTA NRO 2-FRUTAS" is not shadowed by a shorter partial.
MERCADOS = (
    "GRAN MERCADO MAYORISTA DE LIMA",
    "MERCADO MAYORISTA DE AVES VIVAS",
    "MERCADO MAYORISTA NRO 2-FRUTAS",
)
# Only this mercado overlaps our GMML feed; cross_check restricts to it.
GMML_LABEL = "GRAN MERCADO MAYORISTA DE LIMA"
# El mercado de aves (POLLO VIVO) NO existe en el reporte-335: es el único de
# SISAP que se ingesta como serie propia (codigo 'AVES' en store.MERCADOS_CONOCIDOS).
AVES_LABEL = "MERCADO MAYORISTA DE AVES VIVAS"

# x-center bands for the 5 numeric columns (centers ~330/370/430/480/540; the
# bands are generous to absorb minor edition drift, like parser.py's MASA_BANDS).
PRICE_BANDS = (
    ("precio_hoy_kg", 305, 350),
    ("precio_ayer_kg", 350, 400),
    ("diferencia", 400, 455),
    ("prom_mes", 455, 510),
    ("prom_7d", 510, 575),
)
TEXT_MAX_X = 305.0   # tokens left of the first numeric band are mercado+producto
ROW_Y_TOL = 3.0      # merges the ~1px number/text baseline split

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")

_session = requests.Session()
_session.headers["User-Agent"] = UA


# --- helpers -------------------------------------------------------------
def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    """Match key: deaccented, upper, single-spaced. Bridges 'Ajo Criollo O Napuri'
    (DB title case) and 'AJO CRIOLLO O NAPURI' (SISAP upper case)."""
    return re.sub(r"\s+", " ", _deaccent(s).upper()).strip()


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if not _NUM_RE.match(s):
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _center(w) -> float:
    return (w["x0"] + w["x1"]) / 2


def _split_mercado(text: str) -> tuple[str | None, str]:
    """Strip the longest known mercado prefix; return (mercado, producto)."""
    up = _norm(text)
    best = None
    for m in MERCADOS:
        if up.startswith(m) and (best is None or len(m) > len(best)):
            best = m
    if best is None:
        return None, text.strip()
    return best, up[len(best):].strip()


def _extract_fecha(page) -> date | None:
    txt = _deaccent(page.extract_text() or "").lower()
    m = re.search(r"(\d{1,2})\s+de\s+([a-z]+)\s+del?\s+(\d{4})", txt)
    if m and MESES.get(m.group(2)):
        return date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    return None


def _cluster_rows(words):
    rows: list[dict] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        for r in rows:
            if abs(r["top"] - w["top"]) <= ROW_Y_TOL:
                r["items"].append(w)
                break
        else:
            rows.append({"top": w["top"], "items": [w]})
    return rows


# --- fetch ---------------------------------------------------------------
def fetch_sisap(out_dir: str | None = None) -> tuple[str, str, int]:
    """Download the SISAP PDF to RAW_DIR as sisap_<fecha>.pdf.

    Returns (local_path, sha256, nbytes). The filename date is parsed from the
    PDF itself so the cache name reflects the report date, not the wall clock.
    """
    out_dir = out_dir or RAW_DIR
    for k in range(3):
        try:
            r = _session.get(SISAP_URL, timeout=60)
            r.raise_for_status()
            data = r.content
            break
        except requests.RequestException:
            if k == 2:
                raise
    if data[:5] != b"%PDF-":
        raise ValueError("SISAP URL did not return a PDF")

    os.makedirs(out_dir, exist_ok=True)
    # Write to a temp name first so we can read the report date, then rename.
    tmp = os.path.join(out_dir, "sisap_tmp.pdf")
    with open(tmp, "wb") as f:
        f.write(data)
    with pdfplumber.open(tmp) as pdf:
        fecha = _extract_fecha(pdf.pages[0])
    stamp = fecha.isoformat() if fecha else "unknown"
    path = os.path.join(out_dir, f"{FUENTE}_{stamp}.pdf")
    os.replace(tmp, path)
    return path, hashlib.sha256(data).hexdigest(), len(data)


# --- parse ---------------------------------------------------------------
def parse_sisap(pdf_path: str) -> list[dict]:
    """Parse the SISAP PDF into a list of dicts, one per producto row.

    Each dict: {mercado, producto, precio_hoy_kg, precio_ayer_kg, prom_mes,
                prom_7d, fecha}. Prices are already S/ por Kg (no conversion).
    """
    out: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        fecha = _extract_fecha(page)
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
        for r in _cluster_rows(words):
            ws = sorted(r["items"], key=lambda w: w["x0"])
            text_toks = [w["text"] for w in ws if w["x0"] < TEXT_MAX_X]
            num_toks = [w for w in ws if w["x0"] >= TEXT_MAX_X]
            if not text_toks or not num_toks:
                continue

            mercado, producto = _split_mercado(" ".join(text_toks))
            if mercado is None or not producto:
                continue  # header / footer / notes lines

            cols: dict[str, float | None] = {k: None for k, _, _ in PRICE_BANDS}
            for w in num_toks:
                v = _num(w["text"])
                if v is None:
                    continue
                c = _center(w)
                for key, lo, hi in PRICE_BANDS:
                    if lo <= c < hi and cols[key] is None:
                        cols[key] = v
                        break
            # A real data row carries at least the two anchor prices.
            if cols["precio_hoy_kg"] is None and cols["precio_ayer_kg"] is None:
                continue

            out.append({
                "mercado": mercado,
                "producto": producto,
                "precio_hoy_kg": cols["precio_hoy_kg"],
                "precio_ayer_kg": cols["precio_ayer_kg"],
                "prom_mes": cols["prom_mes"],
                "prom_7d": cols["prom_7d"],
                "fecha": fecha,
            })
    return out


# --- cross-check ---------------------------------------------------------
def _fecha_iso(fecha) -> str:
    return fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha)


def _gmml_prices(con, fecha) -> dict[str, float]:
    """{producto normalizado -> precio_hoy_kg} del mercado GMML en `fecha`.

    FILTRA POR MERCADO. Sin el filtro, la consulta barría `precios_diarios`
    entero y traía también las filas de AVES y MMF2, que viven en la misma
    tabla: un producto con el mismo nombre en dos mercados se contrastaría
    contra el precio del mercado equivocado. Hoy no colisiona ningún nombre,
    pero eso es suerte, no una garantía.
    """
    try:
        rows = con.filas(
            "SELECT p.nombre_canonico AS nombre, pr.precio_hoy_kg AS precio "
            "FROM precios_diarios pr "
            "JOIN productos p ON p.id = pr.producto_id "
            "JOIN mercados m ON m.id = pr.mercado_id "
            f"WHERE pr.fecha = {con.ph} AND pr.precio_hoy_kg IS NOT NULL "
            "AND m.codigo = 'GMML'",
            (_fecha_iso(fecha),),
        )
    except Exception:  # noqa: BLE001 - cada driver tiene su jerarquía de errores
        # BD a medio inicializar: es un contraste imposible, no un fallo del
        # cross-check. Degradar a "sin contraparte" mantiene la ingesta GMML en
        # pie, que es la regla de este módulo (no bloquear nunca el pipeline).
        con.rollback()
        return {}
    return {_norm(r["nombre"]): r["precio"] for r in rows}


def _ultima_fecha_gmml(con, antes_de) -> str | None:
    """Última fecha con datos GMML estrictamente anterior a `antes_de`."""
    try:
        valor = con.valor(
            "SELECT MAX(pr.fecha) AS f FROM precios_diarios pr "
            "JOIN mercados m ON m.id = pr.mercado_id "
            f"WHERE m.codigo = 'GMML' AND pr.fecha < {con.ph}",
            (_fecha_iso(antes_de),),
        )
    except Exception:  # noqa: BLE001
        con.rollback()
        return None
    return valor or None


def _resolver_objetivo(con, fecha_sisap) -> dict:
    """Decide CONTRA QUÉ DÍA y con QUÉ COLUMNA de SISAP se contrasta.

    POR QUÉ HACE FALTA ESTO
    -----------------------
    Contrastar siempre la columna "Precio Hoy" de SISAP contra nuestra BD en la
    fecha del propio SISAP hacía que el cross-check NUNCA estuviera vigente.
    SISAP publica ~06:30 y también publica sábados; el reporte-335 del GMML sale
    después y no existe los fines de semana. Resultado observado: SISAP fechado
    el sábado 2026-08-15 contra una BD cuyo último día GMML era el viernes
    2026-08-14 -> cero contrapartes, 0/12 "coinciden", `vigente=false`. La
    redundancia del §4 estaba escrita pero no se ejercía nunca.

    SISAP trae además la columna "Precio Ayer", que describe el MISMO día que
    nuestro último 335. Contrastar esa columna contra ese día compara dos
    fuentes hablando de la misma jornada, que es justo lo que la redundancia
    quiere verificar. No es un parche: es la comparación semánticamente correcta.

    Nunca se compara "Precio Hoy" de SISAP contra un día distinto en nuestra BD:
    eso inventaría divergencias que solo reflejan el paso del tiempo.
    """
    precios = _gmml_prices(con, fecha_sisap)
    if precios:
        return {
            "fecha_gmml": _fecha_iso(fecha_sisap),
            "columna_sisap": "precio_hoy_kg",
            "precios": precios,
            "base": ("mismo día: 'Precio Hoy' de SISAP contra el reporte-335 de "
                     "esa fecha"),
        }

    previa = _ultima_fecha_gmml(con, fecha_sisap)
    if previa:
        return {
            "fecha_gmml": previa,
            "columna_sisap": "precio_ayer_kg",
            "precios": _gmml_prices(con, previa),
            "base": (f"'Precio Ayer' de SISAP ({_fecha_iso(fecha_sisap)}) contra el "
                     f"reporte-335 del {previa}: ambas fuentes describen ese día"),
        }

    return {
        "fecha_gmml": _fecha_iso(fecha_sisap),
        "columna_sisap": "precio_hoy_kg",
        "precios": {},
        "base": "sin ningún día GMML en la BD con el cual contrastar",
    }


def cross_check(sisap_rows: list[dict], db_path: str | None = None) -> list[dict]:
    """Contrasta los precios GMML de SISAP contra nuestro propio feed GMML.

    El día y la columna se resuelven UNA vez con `_resolver_objetivo` (ver ahí
    por qué no siempre es la fecha de SISAP). Devuelve un dict por producto GMML
    presente en SISAP:
      {producto, fecha, fecha_sisap, columna_sisap, sisap_kg, gmml_kg,
       delta_pct, flag, detalle}

    `fecha` es el día GMML CONTRASTADO —no el de SISAP—, porque es el día del que
    la comparación habla; de ahí sale `vigente` en el snapshot.

    `flag` es True cuando |delta| > DELTA_FLAG o el producto no está en nuestra
    BD para ese día: la condición de redundancia que el §4 quiere visible.
    """
    db_path = db_path or DEFAULT_DB
    filas = [r for r in sisap_rows if _norm(r["mercado"]) == _norm(GMML_LABEL)]
    if not filas:
        return []
    fecha_sisap = next((r["fecha"] for r in filas if r.get("fecha")), None)
    if fecha_sisap is None:
        return []

    from .store import LectorDatos, hay_datos

    if hay_datos(db_path):
        # Una sola conexión para todo el contraste. Antes se abría una por fila y
        # se repetía la MISMA consulta invariante dentro del bucle.
        with LectorDatos(db_path) as con:
            objetivo = _resolver_objetivo(con, fecha_sisap)
    else:
        # Sin BD no se contrasta, pero SÍ se reporta fila por fila: cada producto
        # queda como "sin contraparte" y `build_check` lo resume como contraste
        # prematuro. Devolver una lista vacía escondería que SISAP sí trajo datos.
        # `sqlite3.connect` crearía el archivo vacío, así que ni se intenta abrir.
        objetivo = {
            "fecha_gmml": _fecha_iso(fecha_sisap),
            "columna_sisap": "precio_hoy_kg",
            "precios": {},
            "base": "no hay base de datos local con la cual contrastar",
        }

    gmml = objetivo["precios"]
    columna = objetivo["columna_sisap"]
    comun = {
        "fecha": objetivo["fecha_gmml"],
        "fecha_sisap": _fecha_iso(fecha_sisap),
        "columna_sisap": columna,
    }

    out: list[dict] = []
    for row in filas:
        sisap_kg = row.get(columna)
        if sisap_kg is None:
            continue
        gmml_kg = gmml.get(_norm(row["producto"]))

        if gmml_kg is None:
            out.append({
                **comun, "producto": row["producto"],
                "sisap_kg": sisap_kg, "gmml_kg": None, "delta_pct": None,
                "flag": True,
                "detalle": f"sin contraparte GMML del {objetivo['fecha_gmml']} en la BD",
            })
            continue

        base = gmml_kg if gmml_kg else (sisap_kg or 1.0)
        delta = (sisap_kg - gmml_kg) / base if base else 0.0
        flag = abs(delta) > DELTA_FLAG
        detalle = (f"delta {delta*100:+.1f}% > {DELTA_FLAG*100:.0f}%"
                   if flag else f"coincide ({delta*100:+.1f}%)")
        out.append({
            **comun, "producto": row["producto"],
            "sisap_kg": sisap_kg, "gmml_kg": round(gmml_kg, 4),
            "delta_pct": round(delta * 100, 2), "flag": flag, "detalle": detalle,
        })

    return out


# --- persistencia del cross-check (cache junto a la BD) -------------------
def _check_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "sisap_check.json")


_COLUMNA_LABEL = {
    "precio_hoy_kg": "Precio Hoy",
    "precio_ayer_kg": "Precio Ayer",
}


def build_check(cc: list[dict]) -> dict:
    """Resumen serializable del cross-check, listo para el snapshot del dashboard.

    Contrato:
      {fuente, url, fecha, fecha_sisap, columna_sisap, base, umbral_pct,
       contrastados, coinciden, gmml_disponible, resultados[], generado_en}

    `fecha` es el día GMML contrastado, que es del que la comparación habla; el
    dashboard lo usa para decidir `vigente`. `fecha_sisap` y `columna_sisap` se
    exponen aparte para que se vea exactamente qué se comparó contra qué, sin
    tener que confiar en la etiqueta.

    `coinciden` cuenta los NO flageados; cada resultado conserva su detalle
    honesto, incluidos los "sin contraparte GMML".
    """
    fecha = next((r["fecha"] for r in cc if r.get("fecha")), None)
    # Cae a `fecha` si la fila no trae `fecha_sisap`: `build_check` tiene que
    # seguir aceptando cross-checks con la forma anterior (p. ej. un
    # sisap_check.json ya guardado) sin escribir "del None" en la descripción.
    fecha_sisap = next((r["fecha_sisap"] for r in cc if r.get("fecha_sisap")), fecha)
    columna = next((r["columna_sisap"] for r in cc if r.get("columna_sisap")),
                   "precio_hoy_kg")
    resultados = [{
        "producto": r["producto"],
        "sisap_kg": r["sisap_kg"],
        "gmml_kg": r["gmml_kg"],
        "delta_pct": r["delta_pct"],
        "flag": bool(r["flag"]),
        "detalle": r["detalle"],
    } for r in cc]

    iso = (lambda f: f.isoformat() if hasattr(f, "isoformat") else f)  # noqa: E731
    if columna == "precio_ayer_kg":
        base = (f"Columna '{_COLUMNA_LABEL[columna]}' del reporte SISAP del "
                f"{iso(fecha_sisap)} contra el reporte-335 del {iso(fecha)}: ambas "
                f"fuentes describen ese mismo día.")
    else:
        base = (f"Columna '{_COLUMNA_LABEL[columna]}' del reporte SISAP del "
                f"{iso(fecha_sisap)} contra el reporte-335 de la misma fecha.")

    return {
        "fuente": "SISAP – MIDAGRI",
        "url": SISAP_URL,
        "fecha": iso(fecha),
        "fecha_sisap": iso(fecha_sisap),
        "columna_sisap": columna,
        "base": base,
        "umbral_pct": round(DELTA_FLAG * 100),
        "contrastados": len(resultados),
        "coinciden": sum(1 for r in resultados if not r["flag"]),
        # False = la BD no tenía NINGÚN precio GMML con el cual contrastar. Eso es
        # "contraste prematuro", no divergencia — el dashboard lo distingue.
        "gmml_disponible": any(r["gmml_kg"] is not None for r in resultados),
        "resultados": resultados,
        "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def save_check(cc: list[dict], db_path: str | None = None) -> dict:
    """Persiste build_check(cc) junto a la BD (para que export lo adjunte al
    snapshot sin red ni re-parseo). Devuelve el dict guardado. Best-effort:
    un fallo de disco no debe tumbar la ingesta."""
    db_path = db_path or DEFAULT_DB
    check = build_check(cc)
    try:
        with open(_check_path(db_path), "w", encoding="utf-8") as f:
            json.dump(check, f, ensure_ascii=False)
    except OSError:
        pass
    return check


def load_check(db_path: str | None = None) -> dict | None:
    """Lee el cache del cross-check si existe; None si no hay o está corrupto."""
    p = _check_path(db_path or DEFAULT_DB)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# --- CLI -----------------------------------------------------------------
def _main() -> int:
    print(f"Descargando SISAP: {SISAP_URL}")
    path, sha, nbytes = fetch_sisap()
    print(f"  guardado: {path}")
    print(f"  sha256:   {sha}")
    print(f"  bytes:    {nbytes}")

    rows = parse_sisap(path)
    fecha = rows[0]["fecha"] if rows else None
    print(f"\nFecha del reporte: {fecha}   filas: {len(rows)}")
    print(f"\n  {'MERCADO':<32.32} {'PRODUCTO':<22.22} {'HOY':>6} {'AYER':>6} "
          f"{'P.MES':>6} {'P.7D':>6}")
    for r in rows:
        print(f"  {r['mercado']:<32.32} {r['producto']:<22.22} "
              f"{r['precio_hoy_kg']!s:>6} {r['precio_ayer_kg']!s:>6} "
              f"{r['prom_mes']!s:>6} {r['prom_7d']!s:>6}")

    print("\nCross-check GMML (SISAP vs BD propia, mismo dia):")
    cc = cross_check(rows)
    if not cc:
        print("  (sin filas GMML para contrastar)")
    flagged = 0
    for c in cc:
        mark = "[!]" if c["flag"] else "   "
        if c["flag"]:
            flagged += 1
        gmml = "  -  " if c["gmml_kg"] is None else f"{c['gmml_kg']}"
        print(f"  {mark} {c['producto']:<24.24} sisap={c['sisap_kg']!s:>6}  "
              f"gmml={gmml:>6}  {c['detalle']}")
    print(f"\n  {len(cc)} productos GMML contrastados, "
          f"{flagged} marcados (delta > {DELTA_FLAG*100:.0f}%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
