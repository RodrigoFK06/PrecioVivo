"""Harvester for the GMML "Reporte de ingreso y precios" daily PDFs (collection 335).

Navigation (verified): collection page -> monthly index pages (one per month,
?sheet=N for older) -> direct CDN PDF links, one per business day.

Runs locally (the gob.pe WAF does not block this machine). Persists each raw PDF
to data/raw/ (gitignored) with its sha256 for provenance / re-parse (§0.5/§4).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import date

import requests

COLLECTION_URL = ("https://www.gob.pe/institucion/midagri/colecciones/"
                  "335-reporte-de-ingreso-y-precios-en-el-gran-mercado-mayorista-de-lima")
BASE = "https://www.gob.pe"

# Colección 338: el boletín multi-mercado. Añade el Mercado Mayorista de Frutas
# Nº2, que son ~79 productos que el reporte 335 no cubre — su portada lo dice:
# "Hortalizas · Legumbres · Tubérculos y raíces". Las frutas viven aquí.
COLECCION_BOLETIN = ("https://www.gob.pe/institucion/midagri/colecciones/"
                     "338-boletin-de-abastecimiento-y-precios-en-el-mercado-"
                     "mayorista-de-lima-gmml-y-mm-n2")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
RAW_DIR = os.environ.get("PRECIOVIVO_RAW", "../data/raw")
FUENTE = "reporte-335"

# El patrón del PDF es EL MISMO en ambas colecciones: gob.pe pone la fecha al
# final del nombre como DD-MM-AAAA. Lo que cambia es qué páginas de mes buscar.
_PDF_RE = re.compile(
    r"https://cdn\.www\.gob\.pe/uploads/document/file/\d+/[^\"'\s]+?-(\d{2})-(\d{2})-(\d{4})\.pdf(?:\?v=\d+)?", re.I)
_MONTH_RE = re.compile(
    r"/institucion/midagri/informes-publicaciones/\d+-reporte-de-ingreso[a-z0-9-]*", re.I)
_MONTH_RE_BOLETIN = re.compile(
    r"/institucion/midagri/informes-publicaciones/\d+-boletin-de-abastecimiento[a-z0-9-]*", re.I)


@dataclass(frozen=True)
class Coleccion:
    """Una colección de gob.pe: portada, cómo son sus páginas de mes, y su fuente.

    Las dos colecciones que interesan tienen la MISMA estructura de navegación
    —portada paginada con `?sheet=N`, páginas de mes, PDFs con la fecha en el
    nombre— y solo se diferencian en la URL y en el prefijo del slug mensual.
    Por eso el navegador se parametriza en vez de duplicarse: un cambio en cómo
    gob.pe pagina sus colecciones se arregla una vez.
    """
    nombre: str
    url: str
    mes_re: "re.Pattern[str]"
    fuente: str


REPORTE_335 = Coleccion("reporte 335", COLLECTION_URL, _MONTH_RE, "reporte-335")
BOLETIN_338 = Coleccion("boletín 338", COLECCION_BOLETIN, _MONTH_RE_BOLETIN,
                        "boletin-gmml-mm2")

_session = requests.Session()
_session.headers["User-Agent"] = UA


@dataclass
class Daily:
    fecha: date
    url: str


def _get(url: str, tries: int = 3) -> str:
    for k in range(tries):
        try:
            r = _session.get(url, timeout=40)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))
    return ""


def month_pages(max_sheets: int = 4, col: Coleccion = REPORTE_335) -> list[str]:
    """Return month index page URLs (newest first), de-duplicated, across sheets."""
    seen, out = set(), []
    for sheet in range(1, max_sheets + 1):
        url = col.url + ("" if sheet == 1 else f"?sheet={sheet}")
        try:
            html = _get(url)
        except requests.RequestException:
            break
        found = [BASE + m.group(0) for m in col.mes_re.finditer(html)]
        new = [u for u in found if u not in seen]
        if not new:
            break
        for u in new:
            seen.add(u)
            out.append(u)
    return out


def latest_month_page(col: Coleccion = REPORTE_335) -> str | None:
    pages = month_pages(max_sheets=1, col=col)
    # the collection lists newest months first within the page
    return pages[0] if pages else None


def dailies_in_month(month_url: str) -> list[Daily]:
    html = _get(month_url)
    out, seen = [], set()
    for m in _PDF_RE.finditer(html):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            f = date(y, mo, d)
        except ValueError:
            continue
        url = m.group(0)
        if f in seen:
            continue
        seen.add(f)
        out.append(Daily(fecha=f, url=url))
    out.sort(key=lambda x: x.fecha)
    return out


def latest_dailies(n: int = 5, col: Coleccion = REPORTE_335) -> list[Daily]:
    mp = latest_month_page(col)
    if not mp:
        return []
    ds = dailies_in_month(mp)
    return ds[-n:]


def download(daily: Daily) -> tuple[str, str, int]:
    """Download a daily PDF to RAW_DIR. Returns (local_path, sha256, nbytes)."""
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{FUENTE}_{daily.fecha.isoformat()}.pdf")
    for k in range(3):
        try:
            r = _session.get(daily.url, timeout=60)
            r.raise_for_status()
            data = r.content
            break
        except requests.RequestException:
            if k == 2:
                raise
            time.sleep(1.5 * (k + 1))
    if not data[:5] == b"%PDF-":
        raise ValueError(f"{daily.url} did not return a PDF")
    with open(path, "wb") as f:
        f.write(data)
    return path, hashlib.sha256(data).hexdigest(), len(data)
