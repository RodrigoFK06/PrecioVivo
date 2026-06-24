"""Parser for the MIDAGRI "Boletín de abastecimiento y precios mayoristas
GMML y MM N°2" (a SEPARATE series from collection 335).

Why this exists: the daily reporte-335 only covers the Gran Mercado Mayorista de
Lima (GMML). This boletín is MULTI-MERCADO -- it adds the **Mercado Mayorista de
Frutas N°2 (MMF2)** and a richer set of fruit products -- so it is our window
into the FRUTAS market alongside GMML.

PDF shape (~4 pages):
  - pages 1-2: narrative text (ingreso, productos destacados); we ignore them here.
  - pages 3-4: a structured grid, one row per producto x mercado. Columns,
    left -> right (verified on the 12-03-2026 edition):

      CNPA | NOMBRE DEL PRODUCTO | MERCADO | Prom. semana anterior |
      Prom. semana actual (S/ x kg) | Var.(%) | <8 daily prices> | Var.% Hoy/ayer

`pdftotext -layout` scrambles this grid, so -- as in parser.py / sisap.py -- we
work from word x-y geometry instead. We parse by x-center BAND (not flowed
order). One page (the 2nd grid page) renders glyphs spaced out, so a single
logical word like "MERCADO" or "GMML" arrives as several char-words
("GM","M","L"); we join all tokens inside a band before interpreting them, which
makes the mercado/CNPA detection robust to that rendering.

Field chosen for `precio_kg`: the current-week average column ("Prom. ... al jue
N", S/ por kg) -- the boletín's own headline per-kg figure -- not a single daily
tick. `var_pct` is the week-over-week Var.(%) between the two average columns.

Public API:
  parse_boletin(pdf_path) -> list[dict]   (one dict per producto x mercado)
  fetch_boletin(url[, out_dir]) -> (local_path, sha256, nbytes)
  coverage(pdf_path) -> dict              (honest parse-rate report)
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date

import pdfplumber
import requests

BASE_URL = "https://cdn.www.gob.pe/uploads/document/file"
SAMPLE_URL = (
    "https://cdn.www.gob.pe/uploads/document/file/9597599/"
    "7821433-boletin-de-abastecimiento-y-precios-mayoristas-"
    "gmml-y-mm-n-2-12-03-2026.pdf"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
RAW_DIR = os.environ.get("PRECIOVIVO_RAW", "../data/raw")
FUENTE = "boletin-gmml-mm2"

# The mercado marker sits in a narrow x-band between the producto name and the
# first numeric column. On the spaced-glyph page it splits into ("GM","M","L");
# we join every token whose center is in this band before matching.
MERCADO_X0, MERCADO_X1 = 195.0, 215.0
MERCADOS = {
    "GMML": "GMML",
    "MMF2": "MMF2",
}
# Long-form labels for downstream display / cross-source matching.
MERCADO_NOMBRE = {
    "GMML": "Gran Mercado Mayorista de Lima",
    "MMF2": "Mercado Mayorista de Frutas N°2",
}

# CNPA (the product code) lives left of the name; only 5-digit codes are real
# products. Shorter codes (011, 012, 0121, 0132) are category headers, not rows.
CNPA_X_MAX = 82.0
NAME_X0, NAME_X1 = 82.0, 195.0

# x-center bands for the two average columns and the week-over-week Var.(%).
# Generous bands absorb minor edition drift (cf. parser.MASA_BANDS).
PROM_PREV_BAND = (228.0, 252.0)   # Prom. semana anterior   (cx ~240)
PROM_CURR_BAND = (273.0, 298.0)   # Prom. semana actual S/kg (cx ~286)  <- precio_kg
VAR_BAND = (305.0, 328.0)         # Var.(%) prev->curr        (cx ~317)

ROW_Y_TOL = 3.0
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_CNPA_RE = re.compile(r"^\d{5}$")
_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_PCT_RE = re.compile(r"^-?\d+(?:[.,]\d+)?%$")

_session = requests.Session()
_session.headers["User-Agent"] = UA


# --- helpers -------------------------------------------------------------
def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _center(w) -> float:
    return (w["x0"] + w["x1"]) / 2


def _num(s: str) -> float | None:
    s = (s or "").strip().rstrip("%")
    if not _NUM_RE.match(s):
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _clean_name(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s.replace("·", "").strip())


def _extract_fecha(pages) -> date | None:
    """Report date from the narrative cover ("Lima, 12 de marzo del 2026")."""
    for page in pages[:2]:
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


# Gap (px) between consecutive tokens that marks a true word boundary. Within a
# word the spaced-glyph page leaves ~0px gaps; between words the gap is ~0.9px.
WORD_GAP = 0.5


def _join_band(ws, lo: float, hi: float) -> str:
    """Concatenate (in x order) the text of every token centred in [lo, hi).

    Tolerates the spaced-glyph page where one word splits into several tokens.
    """
    toks = [w for w in ws if lo <= _center(w) < hi]
    toks.sort(key=lambda w: w["x0"])
    return "".join(w["text"] for w in toks)


def _join_words(ws, lo: float, hi: float) -> str:
    """Join tokens centred in [lo, hi) into words using the inter-token GAP.

    On the clean page tokens are already whole words (separated by ~1px); on the
    spaced-glyph page a single word arrives as adjacent glyph-tokens (~0px gaps).
    Inserting a space only at gaps >= WORD_GAP reconstructs the real words in
    both cases ("M ARACUYA CO STA" -> "MARACUYA COSTA").
    """
    toks = sorted((w for w in ws if lo <= _center(w) < hi), key=lambda w: w["x0"])
    out = ""
    prev_x1: float | None = None
    for w in toks:
        if prev_x1 is not None and w["x0"] - prev_x1 >= WORD_GAP:
            out += " "
        out += w["text"]
        prev_x1 = w["x1"]
    return out


def _mercado(ws) -> str | None:
    raw = _join_band(ws, MERCADO_X0, MERCADO_X1).replace(" ", "").upper()
    return MERCADOS.get(raw)


def _band_num(ws, lo: float, hi: float) -> float | None:
    """First numeric token centred in [lo, hi)."""
    for w in sorted((w for w in ws if lo <= _center(w) < hi), key=lambda w: w["x0"]):
        v = _num(w["text"])
        if v is not None:
            return v
    return None


# --- fetch ---------------------------------------------------------------
def fetch_boletin(url: str, out_dir: str | None = None) -> tuple[str, str, int]:
    """Download a boletín PDF by direct URL. Returns (local_path, sha256, nbytes).

    There is no stable collection index for this series that we have confirmed,
    so the caller passes the CDN URL (e.g. from gob.pe). The cache filename
    embeds the report date parsed from the PDF, so it reflects the boletín date
    rather than the wall clock.
    """
    out_dir = out_dir or RAW_DIR
    last_exc: Exception | None = None
    for k in range(3):
        try:
            r = _session.get(url, timeout=60)
            r.raise_for_status()
            data = r.content
            break
        except requests.RequestException as e:
            last_exc = e
    else:  # pragma: no cover - exhausted retries
        raise last_exc  # type: ignore[misc]
    if data[:5] != b"%PDF-":
        raise ValueError("la URL no devolvió un PDF")

    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, f"{FUENTE}_tmp.pdf")
    with open(tmp, "wb") as f:
        f.write(data)
    with pdfplumber.open(tmp) as pdf:
        fecha = _extract_fecha(pdf.pages)
    stamp = fecha.isoformat() if fecha else "unknown"
    path = os.path.join(out_dir, f"{FUENTE}_{stamp}.pdf")
    os.replace(tmp, path)
    return path, hashlib.sha256(data).hexdigest(), len(data)


# --- parse ---------------------------------------------------------------
def _parse_data_row(ws, fecha) -> dict | None:
    """Turn one clustered grid row into a product dict, or None if not a row."""
    cnpa_tok = next((w for w in ws if _center(w) < CNPA_X_MAX), None)
    if cnpa_tok is None or not _CNPA_RE.match(cnpa_tok["text"]):
        return None  # category header / title / footer
    mercado = _mercado(ws)
    if mercado is None:
        return None  # a real product row always carries a mercado marker

    precio_kg = _band_num(ws, *PROM_CURR_BAND)
    if precio_kg is None:
        return None  # without the headline S/kg it is not a usable data row

    # Name reconstruction uses the inter-token gap so both the clean page and the
    # spaced-glyph page ("M ARACUYA CO STA") yield "MARACUYA COSTA".
    name = _clean_name(re.sub(r"\s+", " ", _join_words(ws, NAME_X0, NAME_X1)))
    if len(name) < 2:
        return None

    return {
        "mercado": mercado,
        "cnpa": cnpa_tok["text"],
        "producto": name,
        "precio_kg": precio_kg,
        "fecha": fecha,
        "var_pct": _band_num(ws, *VAR_BAND),
    }


def parse_boletin(pdf_path: str) -> list[dict]:
    """Parse the boletín grid into a list of dicts, one per producto x mercado.

    Each dict: {mercado, cnpa, producto, precio_kg, fecha, var_pct}. `precio_kg`
    is the current-week average (S/ por kg) the boletín itself headlines;
    `var_pct` is the week-over-week variation. Rows without a 5-digit CNPA, a
    mercado marker, or a price (category headers, titles, footnotes) are skipped.
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    with pdfplumber.open(pdf_path) as pdf:
        fecha = _extract_fecha(pdf.pages)
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            # The grid lives on the structured pages; narrative pages have no
            # mercado markers in the band, so they naturally yield nothing.
            for r in _cluster_rows(words):
                row = _parse_data_row(r["items"], fecha)
                if row is None:
                    continue
                key = (row["mercado"], row["cnpa"], row["producto"])
                if key in seen:  # guard against a row split across two clusters
                    continue
                seen.add(key)
                out.append(row)
    return out


def coverage(pdf_path: str) -> dict:
    """Honest parse-rate report.

    Compares how many grid rows *look* like data rows (a 5-digit CNPA appears at
    the left edge) against how many we fully parsed (CNPA + mercado + price). The
    gap is rows we dropped -- useful to know exactly what fraction of the grid we
    cover instead of claiming 100%.
    """
    candidate = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            for r in _cluster_rows(words):
                tok = next((w for w in r["items"] if _center(w) < CNPA_X_MAX), None)
                if tok is not None and _CNPA_RE.match(tok["text"]):
                    candidate += 1
    parsed = parse_boletin(pdf_path)
    n_parsed = len(parsed)
    by_merc: dict[str, int] = {}
    for row in parsed:
        by_merc[row["mercado"]] = by_merc.get(row["mercado"], 0) + 1
    pct = round(100.0 * n_parsed / candidate, 1) if candidate else 0.0
    return {
        "candidatos": candidate,   # rows with a 5-digit CNPA (data-row signature)
        "parseados": n_parsed,     # rows we turned into a full dict
        "cobertura_pct": pct,
        "por_mercado": by_merc,
    }


# --- CLI -----------------------------------------------------------------
def _find_sample() -> str | None:
    """Locate a boletín PDF: a cached raw file, then the bundled sample, else None."""
    import glob
    cached = sorted(glob.glob(os.path.join(RAW_DIR, f"{FUENTE}_*.pdf")))
    if cached:
        return cached[-1]
    for cand in ("../data/raw/boletin_12-03-2026.pdf",):
        if os.path.exists(cand):
            return cand
    return None


def _main() -> int:
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = _find_sample()
        if path is None:
            print("Descargando boletín de muestra...")
            try:
                path, sha, nbytes = fetch_boletin(SAMPLE_URL)
                print(f"  guardado: {path}  ({nbytes} bytes, sha256 {sha[:12]}...)")
            except Exception as e:
                print(f"  sin red y sin PDF cacheado: {e}")
                return 1

    rows = parse_boletin(path)
    cov = coverage(path)
    fecha = rows[0]["fecha"] if rows else None
    print(f"\nBoletín: {os.path.basename(path)}")
    print(f"Fecha del reporte: {fecha}   filas: {len(rows)}")
    print(f"\n  {'MERCADO':<6} {'CNPA':<6} {'PRODUCTO':<34.34} {'S//kg':>7} {'Var%':>7}")
    for r in rows:
        var = "" if r["var_pct"] is None else f"{r['var_pct']:+.1f}"
        print(f"  {r['mercado']:<6} {r['cnpa']:<6} {r['producto']:<34.34} "
              f"{r['precio_kg']!s:>7} {var:>7}")

    print(f"\nCobertura: {cov['parseados']}/{cov['candidatos']} filas-grid "
          f"({cov['cobertura_pct']}%)")
    print(f"Por mercado: {cov['por_mercado']}")
    for cod, n in sorted(cov["por_mercado"].items()):
        print(f"  {cod} = {MERCADO_NOMBRE.get(cod, cod)}: {n} productos")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
