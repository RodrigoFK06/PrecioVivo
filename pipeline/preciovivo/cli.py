"""Precio Vivo — CLI.

Inteligencia de precios mayoristas del Gran Mercado Mayorista de Lima (GMML),
en tu terminal: precios del día, movimientos, tendencia con sparkline, pronóstico
con IA y consulta en lenguaje natural. Lee el snapshot que genera el pipeline
(local o, si no, desde la web pública). Hecho por Árkos.

Uso:
    precio                      panorama del día (resumen IA + movers)
    precio ver zanahoria        detalle: precio, tendencia, sparkline, pronóstico
    precio pregunta "¿qué subió más hoy?"   consulta en lenguaje natural (IA)
    precio resumen              el resumen del día redactado por IA
    precio alertas              variaciones fuertes y anomalías
    precio tabla --buscar papa  tabla de productos (filtrable)
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
import urllib.request
from pathlib import Path

# Windows: fuerza UTF-8 en la salida para que sparklines/flechas/acentos no
# revienten cuando el codepage de consola no es UTF-8 (o la salida va a un pipe).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

import typer
from rich.box import SIMPLE_HEAD
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Marca Árkos
TEAL = "rgb(82,210,215)"
INDIGO = "rgb(120,110,180)"  # índigo aclarado para legibilidad en terminal oscura
UP = "red"      # precio sube = rojo (convención financiera, igual que la web)
DOWN = "green"  # precio baja = verde

SNAPSHOT_URL = "https://precio-vivo.vercel.app/snapshot.json"
BLOCKS = "▁▂▃▄▅▆▇█"

console = Console()
app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    help="Precio Vivo — precios mayoristas del GMML con IA, en tu terminal. [dim]por Árkos[/dim]",
)


# --------------------------------------------------------------------------- #
# Carga de datos
# --------------------------------------------------------------------------- #
def _snapshot_local() -> Path:
    # cli.py -> preciovivo -> pipeline -> raíz del repo -> web/data/snapshot.json
    return Path(__file__).resolve().parents[2] / "web" / "data" / "snapshot.json"


def cargar_snapshot() -> dict:
    """Snapshot local si existe; si no, lo baja de la web pública."""
    env = os.environ.get("PRECIOVIVO_SNAPSHOT")
    if env and Path(env).exists():
        return json.loads(Path(env).read_text(encoding="utf-8"))
    local = _snapshot_local()
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(SNAPSHOT_URL, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        console.print(f"[red]No pude cargar los datos[/red] (ni local ni {SNAPSHOT_URL}): {e}")
        raise typer.Exit(1)


# --------------------------------------------------------------------------- #
# Formato
# --------------------------------------------------------------------------- #
def soles(x) -> str:
    try:
        return f"S/ {float(x):.2f}"
    except (TypeError, ValueError):
        return "S/ —"


def pct_text(v) -> Text:
    if v is None:
        return Text("—", style="dim")
    color = UP if v > 0 else DOWN if v < 0 else "dim"
    flecha = "▲" if v > 0 else "▼" if v < 0 else "·"
    signo = "+" if v > 0 else ""
    return Text(f"{flecha} {signo}{v:.1f}%", style=color)


def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "setiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(iso: str) -> str:
    try:
        a, m, d = iso.split("-")
        return f"{int(d)} de {_MESES[int(m) - 1]} de {a}"
    except Exception:
        return iso or "—"


def sparkline(valores) -> str:
    vals = [v for v in valores if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return BLOCKS[0] * len(vals)
    return "".join(BLOCKS[int((v - lo) / (hi - lo) * (len(BLOCKS) - 1))] for v in vals)


def buscar_producto(snap: dict, query: str) -> dict | None:
    q = _deaccent((query or "").lower()).strip()
    prods = snap["productos"]
    for p in prods:  # slug exacto
        if p["slug"] == q:
            return p
    cand = [p for p in prods if q in _deaccent(p["nombre"].lower())]
    return cand[0] if cand else None


def banner(snap: dict) -> None:
    console.print()
    titulo = Text()
    titulo.append("Precio Vivo", style="bold white")
    titulo.append("  ·  ", style="dim")
    titulo.append("por Árkos", style=f"bold {TEAL}")
    console.print(titulo)
    console.print(f"[dim]{snap.get('mercado','GMML')} · {fecha_larga(snap.get('latestFecha',''))}[/dim]")
    console.print()


def _movers_table(titulo: str, filas: list[dict], color: str) -> Table:
    t = Table(box=SIMPLE_HEAD, expand=False, pad_edge=False, show_edge=False)
    t.add_column(Text(titulo, style=f"bold {color}"), no_wrap=True)
    t.add_column("S/ kg", justify="right", no_wrap=True)
    t.add_column("Δ", justify="right", no_wrap=True)
    for p in filas:
        lat = p["latest"]
        t.add_row(p["nombre"][:26], soles(lat["precio_kg"]), pct_text(lat.get("var_pct")))
    return t


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
def _cmd_hoy(snap: dict) -> None:
    banner(snap)

    resumen = (snap.get("resumenIA") or {}).get("texto")
    if resumen:
        fuente = (snap.get("resumenIA") or {}).get("fuente")
        etiqueta = "IA" if fuente == "llm" else "reglas"
        console.print(Panel(resumen, title=f"[{TEAL}]Resumen del día[/] [dim]· {etiqueta}[/dim]",
                            border_style="grey37", padding=(1, 2)))
        console.print()

    con_var = [p for p in snap["productos"] if p["latest"].get("var_pct") is not None]
    sube = sorted(con_var, key=lambda p: p["latest"]["var_pct"], reverse=True)[:6]
    baja = sorted(con_var, key=lambda p: p["latest"]["var_pct"])[:6]
    console.print(Columns(
        [_movers_table("▲ Subieron", sube, UP), _movers_table("▼ Bajaron", baja, DOWN)],
        equal=True, expand=False, padding=(0, 4),
    ))
    console.print()
    n = snap.get("productCount", len(snap["productos"]))
    console.print(f"[dim]{n} productos · pruébalo:[/dim] "
                  f"[{TEAL}]precio ver zanahoria[/]  ·  "
                  f"[{TEAL}]precio pregunta \"¿qué está más barato hoy?\"[/]")
    console.print()


@app.command("hoy", help="Panorama del día: resumen IA + mayores movimientos.")
def hoy() -> None:
    _cmd_hoy(cargar_snapshot())


@app.command("ver", help="Detalle de un producto: precio, tendencia, sparkline y pronóstico.")
def ver(producto: str = typer.Argument(..., help="Nombre o slug, p. ej. 'zanahoria'.")) -> None:
    snap = cargar_snapshot()
    p = buscar_producto(snap, producto)
    if not p:
        console.print(f"[red]No encontré[/red] '{producto}'. Prueba [{TEAL}]precio tabla[/] para ver los nombres.")
        raise typer.Exit(1)

    lat = p["latest"]
    head = Text()
    head.append(p["nombre"], style="bold white")
    head.append(f"  ·  {p.get('unidad','')}", style="dim")
    console.print()
    console.print(head)

    linea = Text()
    linea.append(f"{soles(lat['precio_kg'])} / kg", style="bold")
    linea.append("    ")
    linea.append_text(pct_text(lat.get("var_pct")))
    linea.append(f"   tendencia: {lat.get('tendencia') or '—'}", style="dim")
    console.print(linea)
    masa = lat.get("masa_hoy")
    if masa is not None:
        console.print(f"[dim]Ingreso hoy: {masa:.0f} t[/dim]")

    serie = [pt["precio_kg"] for pt in p["series"][-60:]]
    spark = sparkline(serie)
    if spark:
        console.print()
        console.print(f"[dim]Precio (60 días)[/dim]  [{TEAL}]{spark}[/]")

    fc = p.get("forecast")
    if fc and fc.get("precio_estimado") is not None:
        lo, hi = (fc.get("intervalo") or [None, None])[:2]
        rango = f"  [dim][{soles(lo)} – {soles(hi)}][/dim]" if lo is not None else ""
        console.print()
        cuerpo = Text()
        cuerpo.append("Pronóstico (mañana): ", style="dim")
        cuerpo.append(soles(fc["precio_estimado"]), style=f"bold {TEAL}")
        console.print(cuerpo, end="")
        console.print(rango)
        mm, mb = fc.get("mae_modelo"), fc.get("mae_baseline")
        if mm is not None and mb is not None:
            if mm < mb:
                console.print(f"[green]✓ el modelo le gana al baseline[/green] "
                              f"[dim](MAE {mm:.2f} vs {mb:.2f})[/dim]")
            else:
                console.print(f"[yellow]~ aquí el modelo no le gana al baseline[/yellow] "
                              f"[dim](MAE {mm:.2f} vs {mb:.2f})[/dim]")
    console.print()


def _recuperador(snap: dict):
    """Carga el índice RAG publicado, o None si no se puede.

    Devuelve (recuperador, motivo). `motivo` explica en una línea POR QUÉ no se
    pudo, para poder decírselo al usuario en vez de degradar en silencio: un
    asistente que calla que perdió la mitad de sus capacidades es peor que uno
    que avisa.
    """
    try:
        from .retrieval import Recuperador
        return Recuperador.desde_artefacto(snap), None
    except Exception as e:  # noqa: BLE001 - la CLI nunca debe caerse por esto
        return None, str(e).split("\n")[0]


def _catalogo(snap: dict) -> list[dict]:
    """Catálogo plano (GMML + otros mercados) con los hechos numéricos reales."""
    cat = [{"slug": p["slug"], "nombre": p["nombre"],
            "precio_kg": p["latest"].get("precio_kg"),
            "var_pct": p["latest"].get("var_pct"),
            "tendencia": p["latest"].get("tendencia"),
            "pronostico_kg": (p.get("forecast") or {}).get("precio_estimado"),
            "mercado": "GMML"} for p in snap["productos"]]
    for m in snap.get("mercados", []):
        for p in m["productos"]:
            if p.get("precio_kg") is not None:
                cat.append({"slug": p["slug"], "nombre": p["nombre"],
                            "precio_kg": p["precio_kg"], "var_pct": p.get("var_pct"),
                            "tendencia": None, "pronostico_kg": None,
                            "mercado": m["nombre"]})
    return cat


@app.command("contexto",
             help="Muestra QUÉ recupera el RAG para una pregunta (sin llamar al modelo).")
def contexto(
    texto: str = typer.Argument(..., help='p. ej. "¿por qué subió la papa esta semana?"'),
    k: int = typer.Option(6, "--k", help="Cuántos chunks recuperados mostrar."),
    completo: bool = typer.Option(False, "--completo", help="Imprime el texto entero."),
) -> None:
    snap = cargar_snapshot()
    rec, motivo = _recuperador(snap)
    if rec is None:
        console.print(f"[red]Sin índice RAG.[/red] {motivo}")
        raise typer.Exit(1)

    ctx = rec.recuperar(texto, k=k)
    c = ctx.consulta
    console.print()
    console.print(f"[bold white]{texto}[/bold white]")
    detalle = Text()
    detalle.append("productos: ", style="dim")
    detalle.append(", ".join(sorted(c.slugs)) if c.slugs else "—", style=TEAL)
    detalle.append("   fechas: ", style="dim")
    detalle.append(f"{c.fecha_desde or '—'} … {c.fecha_hasta or '—'}", style=TEAL)
    console.print(detalle)
    console.print(f"[dim]{len(rec.chunks)} chunks indexados · "
                  f"{ctx.n_candidatos} candidatos fusionados · "
                  f"piso {len(ctx.piso)} · recuperados {len(ctx.recuperados)}[/dim]")
    console.print()

    def _muestra(titulo: str, filas: list[tuple[str, str, float | None]]) -> None:
        if not filas:
            return
        t = Table(box=SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True)
        t.add_column(Text(titulo, style=f"bold {TEAL}"), no_wrap=True)
        t.add_column("Extracto", overflow="fold")
        if any(s is not None for _, _, s in filas):
            t.add_column("Score", justify="right", no_wrap=True)
        for cid, cuerpo, score in filas:
            extracto = cuerpo if completo else cuerpo.split("\n")[0][:96]
            fila = [cid[:44], extracto]
            if any(s is not None for _, _, s in filas):
                fila.append(f"{score:.3f}" if score is not None else "—")
            t.add_row(*fila)
        console.print(t)
        console.print()

    _muestra("Garantizado (piso determinista)",
             [(x.id, x.texto, None) for x in ctx.piso])
    vistos = {x.id for x in ctx.piso}
    _muestra("Recuperado (búsqueda híbrida)",
             [(r.chunk.id, r.chunk.texto, r.score)
              for r in ctx.recuperados if r.chunk.id not in vistos])


@app.command("pregunta", help="Pregunta en lenguaje natural sobre los precios (IA + RAG).")
def pregunta(texto: str = typer.Argument(..., help='p. ej. "¿qué frutas subieron esta semana?"')) -> None:
    from . import ai
    snap = cargar_snapshot()
    catalogo = [{"slug": p["slug"], "nombre": p["nombre"],
                 "precio_kg": p["latest"].get("precio_kg"),
                 "var_pct": p["latest"].get("var_pct"),
                 "tendencia": p["latest"].get("tendencia"),
                 "pronostico_kg": (p.get("forecast") or {}).get("precio_estimado"),
                 "mercado": "GMML"} for p in snap["productos"]]
    # Otros mercados del snapshot (pollo vivo vía SISAP, frutas del boletín):
    # también son datos reales — sin esto, "¿cuánto está el pollo?" negaría
    # un precio que el dashboard sí muestra.
    for m in snap.get("mercados", []):
        for p in m["productos"]:
            if p.get("precio_kg") is not None:
                catalogo.append({"slug": p["slug"], "nombre": p["nombre"],
                                 "precio_kg": p["precio_kg"],
                                 "var_pct": p.get("var_pct"),
                                 "tendencia": None, "pronostico_kg": None,
                                 "mercado": m["nombre"]})
    # Contexto de verificación (§4): el asistente debe poder responder "¿esto
    # está corroborado?" con el cross-check SISAP del día, no con un "no sé".
    v = snap.get("verificacion") or {}
    contexto = None
    if v.get("contrastados"):
        contexto = (f"Los precios GMML se contrastan a diario contra SISAP-MIDAGRI "
                    f"(publicación independiente). Último contraste ({v.get('fecha')}): "
                    f"{v.get('coinciden')}/{v.get('contrastados')} coinciden dentro de "
                    f"±{v.get('umbral_pct')}%.")
        if not v.get("gmml_disponible"):
            contexto += " (El contraste de ese día quedó pendiente de completar.)"
    # RAG: si hay índice publicado, el modelo recibe la EVIDENCIA TEMPORAL
    # (ventanas, anomalías, ficha) en vez de una línea por producto. Sin índice
    # se degrada al camino anterior, y se avisa: callar que se perdió la mitad
    # de la capacidad es peor que decirlo.
    rec, motivo = _recuperador(snap)
    with console.status(f"[{TEAL}]Pensando…[/]", spinner="dots"):
        if rec is not None:
            ctx = rec.recuperar(texto, k=8)
            res = ai.answer_with_context(texto, ctx.a_prompt(), catalogo)
            res["_rag"] = (len(ctx.piso), len(ctx.recuperados))
        else:
            res = ai.answer_market_question(texto, catalogo, contexto=contexto)

    console.print()
    if rec is None:
        console.print(f"[dim]sin RAG ({motivo}) · "
                      f"`python -m preciovivo.ingest --index` lo activa[/dim]")
    icono = "🤖" if res.get("fuente", "").startswith("llm") else "•"
    console.print(Panel(res["texto"], title=f"{icono} [{TEAL}]Respuesta[/]",
                        border_style="grey37", padding=(1, 2)))
    if res.get("_rag"):
        piso, recs = res["_rag"]
        console.print(f"[dim]contexto: {piso} hechos garantizados + {recs} "
                      f"recuperados · `precio contexto \"…\"` para verlos[/dim]")
    # Filas planas (nombre, precio, var) para GMML y otros mercados por igual.
    por_slug = {p["slug"]: {"nombre": p["nombre"],
                            "precio_kg": p["latest"].get("precio_kg"),
                            "var_pct": p["latest"].get("var_pct")}
                for p in snap["productos"]}
    for m in snap.get("mercados", []):
        for p in m["productos"]:
            por_slug.setdefault(p["slug"], {"nombre": f"{p['nombre']} · {m['nombre']}",
                                            "precio_kg": p.get("precio_kg"),
                                            "var_pct": p.get("var_pct")})
    rel = [por_slug[s] for s in res.get("slugs", []) if s in por_slug]
    if rel:
        t = Table(box=SIMPLE_HEAD, show_edge=False, pad_edge=False)
        t.add_column("Producto", no_wrap=True)
        t.add_column("S/ kg", justify="right")
        t.add_column("Δ ayer", justify="right")
        for p in rel:
            t.add_row(p["nombre"][:30], soles(p["precio_kg"]),
                      pct_text(p.get("var_pct")))
        console.print(t)
    console.print()


@app.command("resumen", help="El resumen del día redactado por IA.")
def resumen() -> None:
    snap = cargar_snapshot()
    r = snap.get("resumenIA") or {}
    if not r.get("texto"):
        console.print("[dim]Sin resumen disponible.[/dim]")
        raise typer.Exit()
    banner(snap)
    etiqueta = "redactado por IA" if r.get("fuente") == "llm" else "generado por reglas"
    console.print(Panel(r["texto"], title=f"[{TEAL}]Resumen del día[/] [dim]· {etiqueta}[/dim]",
                        border_style="grey37", padding=(1, 2)))
    console.print()


@app.command("alertas", help="Variaciones fuertes y anomalías estadísticas del día.")
def alertas(limite: int = typer.Option(8, "--limite", "-n", help="Cuántas mostrar.")) -> None:
    snap = cargar_snapshot()
    al = snap.get("alertas") or []
    banner(snap)
    if not al:
        console.print("[dim]Sin alertas hoy.[/dim]")
        raise typer.Exit()
    t = Table(box=SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True)
    t.add_column("Producto", no_wrap=True, style="bold")
    t.add_column("Señal", justify="right", no_wrap=True)
    t.add_column("Detalle", overflow="fold")
    for a in al[:limite]:
        nombre = a.get("producto") or a.get("nombre") or "—"
        señal = a.get("titulo") or a.get("tipo") or ""
        detalle = a.get("detalle") or a.get("mensaje") or a.get("texto") or ""
        t.add_row(str(nombre)[:24], Text(str(señal)[:14], style=TEAL), str(detalle))
    console.print(t)
    console.print()


@app.command("tabla", help="Tabla de productos (precio, Δ, tendencia, ingreso, pronóstico).")
def tabla(
    buscar: str = typer.Option("", "--buscar", "-b", help="Filtra por nombre."),
    top: int = typer.Option(0, "--top", "-n", help="Solo N filas (0 = todas)."),
) -> None:
    snap = cargar_snapshot()
    banner(snap)
    prods = sorted(snap["productos"], key=lambda p: p["nombre"])
    if buscar:
        q = _deaccent(buscar.lower())
        prods = [p for p in prods if q in _deaccent(p["nombre"].lower())]
    if top > 0:
        prods = prods[:top]
    t = Table(box=SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=True)
    t.add_column("Producto", no_wrap=True)
    t.add_column("S/ kg", justify="right")
    t.add_column("Δ ayer", justify="right")
    t.add_column("Tendencia", no_wrap=True)
    t.add_column("Ingreso", justify="right")
    t.add_column("Pronóstico", justify="right")
    for p in prods:
        lat = p["latest"]
        fc = p.get("forecast") or {}
        masa = lat.get("masa_hoy")
        t.add_row(
            p["nombre"][:30],
            soles(lat["precio_kg"]),
            pct_text(lat.get("var_pct")),
            Text(lat.get("tendencia") or "—", style="dim"),
            f"{masa:.0f} t" if masa is not None else "—",
            soles(fc["precio_estimado"]) if fc.get("precio_estimado") is not None else "—",
        )
    console.print(t)
    console.print(f"[dim]{len(prods)} productos[/dim]\n")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _cmd_hoy(cargar_snapshot())


if __name__ == "__main__":
    app()
