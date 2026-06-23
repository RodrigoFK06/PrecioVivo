"""Coordinate-based parser for the GMML "Reporte de ingreso y precios" daily PDF
(MIDAGRI collection 335). `pdftotext -layout` scrambles this layout, so we work
from word/char x-y geometry instead.

Per-product columns, in a FIXED left-to-right order:
  PRODUCTO | Masa Ingreso(t): Ayer Hoy Últ7 Últ4lun | Unidad | Equiv-Kg
           | Precio S/ x Unidad: Ayer Hoy | (Precio Últ7 + Tendencia, glued)

We parse by relative ORDER + token TYPE rather than absolute x-positions, because
column pixel positions drift ~10px between editions (verified 2024 vs 2026). This
makes the parser robust to that drift. The last visual column glues the 7-day
price and the trend word into one token (e.g. "4.E1s8table" = 4.18 + Estable);
we split it by character type (digits/'.' -> price, letters -> trend).

Public API: parse_report(pdf_path) -> ParseResult
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import pdfplumber

# Loose horizontal guards (generous; absorb >10px edition drift). center = (x0+x1)/2.
NAME_MAX = 232          # product-name tokens have center < this
MASA_MIN = 180          # masa values start here; footnote markers ("1/","2/") sit left of it
MASA_MAX = 372          # the 4 masa values sit left of the unidad text
# The 4 masa columns are right-aligned and ~30px apart; assign by x-center band
# (NOT by collection order) so blank missing cells don't shift the assignment.
MASA_BANDS = (("ayer", 200, 267), ("hoy", 267, 297), ("d7", 297, 333), ("d4lun", 333, 372))
ROW_Y_TOL = 3.0
MISSING = {":", "-", "", "—", "·"}

TENDENCIAS = {
    "estable": "Estable",
    "enalza": "En Alza",
    "enbaja": "En Baja",
    "bajanotable": "Baja Notable",
}

NON_PRODUCT = re.compile(
    r"^(productos?|masa|precios?|promedio|unidad|equiv|ayer|hoy|"
    r"d.as|medida|kg|.lt|total|fuente|elaborac|nota|de\b|en\b|"
    r"lunes|martes|mi.rcoles|jueves|viernes|s.bado|domingo)",  # weekday in date header
    re.IGNORECASE,
)

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _has_alpha(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _is_missing(s: str) -> bool:
    return s.strip() in MISSING


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if _is_missing(s):
        return None
    s2 = re.sub(r"[^\d.]", "", s.replace(",", ""))
    if not s2 or s2 == ".":
        return None
    try:
        return float(s2)
    except ValueError:
        return None


def _clean_name(s: str) -> str:
    s = s.replace(":", "").replace("·", "")
    return re.sub(r"\s{2,}", " ", s).strip()


def _center(w) -> float:
    return (w["x0"] + w["x1"]) / 2


@dataclass
class ProductRow:
    producto: str
    masa_ayer: float | None
    masa_hoy: float | None
    masa_7d: float | None
    masa_4lun: float | None
    unidad: str
    equiv_kg: float | None
    precio_ayer_unit: float | None
    precio_hoy_unit: float | None
    precio_7d_unit: float | None
    tendencia: str | None
    precio_ayer_kg: float | None = None
    precio_hoy_kg: float | None = None
    precio_7d_kg: float | None = None

    def __post_init__(self):
        for src, dst in (("precio_ayer_unit", "precio_ayer_kg"),
                         ("precio_hoy_unit", "precio_hoy_kg"),
                         ("precio_7d_unit", "precio_7d_kg")):
            pu = getattr(self, src)
            if pu is not None and self.equiv_kg:
                setattr(self, dst, round(pu / self.equiv_kg, 4))


@dataclass
class ParseResult:
    fecha: date | None
    rows: list[ProductRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fecha is not None and len(self.rows) >= 40 and not self.warnings


def _extract_fecha(page) -> date | None:
    txt = _deaccent(page.extract_text() or "").lower()
    m = re.search(r"(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})", txt)
    if m and MESES.get(m.group(2)):
        return date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    return None


def _cluster_rows(items):
    rows: list[dict] = []
    for it in sorted(items, key=lambda w: (round(w["top"], 1), w["x0"])):
        for r in rows:
            if abs(r["top"] - it["top"]) <= ROW_Y_TOL:
                r["items"].append(it)
                break
        else:
            rows.append({"top": it["top"], "items": [it]})
    return rows


def _split_price_trend(chars_in_band):
    """Separate the glued precio_7d + tendencia char band into (price, trend)."""
    digits, letters = [], []
    for c in sorted(chars_in_band, key=lambda c: c["x0"]):
        t = c["text"]
        if t.isdigit() or t == ".":
            digits.append(t)
        elif t.isalpha():
            letters.append(_deaccent(t).lower())
    return _num("".join(digits)), TENDENCIAS.get("".join(letters)), "".join(letters)


def _parse_row(ws, chars, top, res: ParseResult):
    ws = sorted(ws, key=lambda w: w["x0"])
    i, n = 0, len(ws)

    # 1) name: leading tokens with letters, left of the masa block
    name_toks = []
    while i < n and _center(ws[i]) < NAME_MAX and _has_alpha(ws[i]["text"]) and not _has_digit(ws[i]["text"]):
        name_toks.append(ws[i]["text"])
        i += 1
    if not name_toks:
        return
    name = _clean_name(" ".join(name_toks))
    if len(name) < 3 or "," in name or NON_PRODUCT.match(_deaccent(name)):
        return  # commas only appear in the date header, never in product names

    # skip footnote markers ("1/", "2/") that sit in the gap before the masa block
    while i < n and _center(ws[i]) < MASA_MIN:
        i += 1

    # 2) masa: assign the 4 columns BY X-BAND, not by collection order — some
    #    editions leave missing cells blank (no ":"), which would otherwise shift
    #    a positional assignment and store values in the wrong column.
    masa = {"ayer": None, "hoy": None, "d7": None, "d4lun": None}
    masa_ambiguous = False
    while i < n and _center(ws[i]) < MASA_MAX and not _has_alpha(ws[i]["text"]) \
            and (_is_missing(ws[i]["text"]) or _has_digit(ws[i]["text"])):
        c = _center(ws[i])
        for key, lo, hi in MASA_BANDS:
            if lo <= c < hi:
                if masa[key] is None:
                    masa[key] = _num(ws[i]["text"])
                else:
                    masa_ambiguous = True
                break
        i += 1

    # 3) unidad: alpha tokens (e.g. "Kilogramo", "Atado Grande")
    unidad_toks = []
    while i < n and _has_alpha(ws[i]["text"]) and not _has_digit(ws[i]["text"]):
        unidad_toks.append(ws[i]["text"])
        i += 1

    # 4) equiv, precio_ayer, precio_hoy: next clean numerics; STOP at the glued
    #    precio_7d+tendencia token (it contains letters).
    nums = []
    while i < n and len(nums) < 3 and not _has_alpha(ws[i]["text"]):
        nums.append(ws[i])
        i += 1
    right_anchor = nums[-1]["x1"] if nums else (ws[i - 1]["x1"] if i else 9999)

    # 5) rightband: char-level split of the glued precio_7d + tendencia
    right_chars = [c for c in chars
                   if abs(c["top"] - top) <= ROW_Y_TOL and c["x0"] > right_anchor - 1]
    p7, tend, raw = _split_price_trend(right_chars)

    def vget(lst, k):
        if k >= len(lst):
            return ""
        el = lst[k]
        return el if isinstance(el, str) else el["text"]

    row = ProductRow(
        producto=name,
        masa_ayer=masa["ayer"], masa_hoy=masa["hoy"],
        masa_7d=masa["d7"], masa_4lun=masa["d4lun"],
        unidad=_clean_name(" ".join(unidad_toks)) or "?",
        equiv_kg=_num(vget(nums, 0)),
        precio_ayer_unit=_num(vget(nums, 1)),
        precio_hoy_unit=_num(vget(nums, 2)),
        precio_7d_unit=p7,
        tendencia=tend,
    )
    if masa_ambiguous:
        res.warnings.append(f"{name}: two masa values fell in one column band")
    if row.unidad == "?":
        res.warnings.append(f"{name}: missing unidad")
    if tend is None and raw:
        res.warnings.append(f"{name}: unknown tendencia '{raw}'")
    if row.precio_hoy_kg is not None and not (0.2 <= row.precio_hoy_kg <= 60):
        res.warnings.append(f"{name}: precio_hoy_kg out of range ({row.precio_hoy_kg})")
    res.rows.append(row)


def parse_report(pdf_path: str) -> ParseResult:
    res = ParseResult(fecha=None)
    with pdfplumber.open(pdf_path) as pdf:
        res.fecha = _extract_fecha(pdf.pages[0])
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            chars = page.chars
            for r in _cluster_rows(words):
                _parse_row(r["items"], chars, r["top"], res)
    return res


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/samples/reporte_335_2026-06-22.pdf"
    r = parse_report(path)
    print(f"fecha={r.fecha}  productos={len(r.rows)}  warnings={len(r.warnings)}  ok={r.ok}")
    print("\n  PRODUCTO                  masa(A/H/7/4L)        unidad        eqv  p_hoy/u  p_hoy/kg  tend")
    for p in r.rows[:26]:
        masa = "/".join("·" if v is None else f"{v:g}" for v in
                        (p.masa_ayer, p.masa_hoy, p.masa_7d, p.masa_4lun))
        print(f"  {p.producto:<24.24} {masa:<20.20} {p.unidad:<12.12} {p.equiv_kg!s:>5} "
              f"{p.precio_hoy_unit!s:>7} {p.precio_hoy_kg!s:>8}  {p.tendencia}")
    if r.warnings:
        print("\nWARNINGS:")
        for w in r.warnings[:20]:
            print("  -", w)
