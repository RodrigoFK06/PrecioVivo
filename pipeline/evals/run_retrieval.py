"""Evaluación de RECUPERACIÓN de Precio Vivo.

Mide si la búsqueda híbrida trae los hechos correctos, y falla con código de
salida 1 cuando el recall cae por debajo del umbral. Es la semilla de la suite
del bloque 4 (que sumará calidad de RESPUESTA, no solo de recuperación).

Uso:
    python evals/run_retrieval.py                         # granularidad semana
    python evals/run_retrieval.py --granularidad todas    # compara dia/semana/mes
    python evals/run_retrieval.py --embedder local --k 10 --umbral 0.8

POR QUÉ EXISTE
--------------
Sin esto, "RAG real" es una afirmación que nadie puede falsear. Y en particular:
la elección de granularidad SEMANAL se defiende en el README con los números que
imprime `--granularidad todas`, no con una intuición.

SOBRE EL EMBEBEDOR
------------------
Los números dependen de cuál se use, así que el reporte SIEMPRE lo dice. Con
`fake` se mide la mecánica (filtros, piso, fusión) sin red ni modelo — útil en
CI; con `local` o `api` se mide calidad semántica de verdad. Comparar cifras de
embebedores distintos no tiene sentido y el encabezado lo deja explícito.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

# Ejecutable directamente desde pipeline/ sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from estadistica import diagnostico, linea_mde  # noqa: E402

from preciovivo.corpus import GRANULARIDADES, Chunk  # noqa: E402
from preciovivo.embeddings import get_embedder  # noqa: E402
from preciovivo.retrieval import Recuperador  # noqa: E402

GOLD = Path(__file__).resolve().parent / "retrieval_gold.json"
SNAPSHOT = Path(__file__).resolve().parents[2] / "web" / "data" / "snapshot.json"


# --------------------------------------------------------------------------- #
# Predicados
# --------------------------------------------------------------------------- #
def cumple(chunk: Chunk, pred: dict) -> bool:
    """¿Este chunk satisface el predicado del gold set?

    Los predicados son deliberadamente granularity-independent: se pide que el
    chunk CUBRA una fecha, no que tenga un id concreto. Así el mismo gold set
    evalúa día, semana y mes sin reescribirse.
    """
    if "id" in pred and chunk.id != pred["id"]:
        return False
    if "slug" in pred and chunk.slug != pred["slug"]:
        return False
    if "tipo" in pred and chunk.tipo != pred["tipo"]:
        return False
    if "cubre_fecha" in pred:
        f = pred["cubre_fecha"]
        if not (chunk.fecha_inicio and chunk.fecha_fin
                and chunk.fecha_inicio <= f <= chunk.fecha_fin):
            return False
    # noqa: SIM103 - la cadena de guardas se lee mejor que una sola expresión
    # booleana con cinco condiciones negadas.
    if ("texto_contiene" in pred  # noqa: SIM103
            and pred["texto_contiene"].lower() not in chunk.texto.lower()):
        return False
    return True


def _span_dias(c: Chunk) -> int | None:
    """Cuántos días calendario cubre el chunk. Menos = evidencia más ajustada."""
    if not (c.fecha_inicio and c.fecha_fin):
        return None
    try:
        return (date.fromisoformat(c.fecha_fin)
                - date.fromisoformat(c.fecha_inicio)).days + 1
    except ValueError:
        return None


def evaluar_caso(caso: dict, chunks: list[Chunk]) -> dict:
    """Métricas de un caso sobre la lista de chunks recuperados (en orden)."""
    esperados = caso.get("debe_recuperar") or []
    prohibidos = caso.get("no_debe_recuperar") or []

    satisfechos = sum(1 for p in esperados if any(cumple(c, p) for c in chunks))
    recall = (satisfechos / len(esperados)) if esperados else None

    rr = 0.0
    for i, c in enumerate(chunks, start=1):
        if any(cumple(c, p) for p in esperados):
            rr = 1.0 / i
            break

    # ESPECIFICIDAD TEMPORAL. recall y MRR miden si el chunk correcto SE
    # ENCUENTRA, no si sirve para responder. Un chunk mensual que cubre el día
    # pedido satisface el predicado igual que uno semanal, pero le entrega al
    # modelo veinte días promediados en vez de cinco: el pico que la pregunta
    # busca queda diluido. Esta métrica mide justamente eso — cuántos días cubre
    # la evidencia que se recuperó. Menos es mejor.
    spans: list[int] = []
    for p in esperados:
        if "cubre_fecha" not in p:
            continue
        for c in chunks:
            if cumple(c, p):
                s = _span_dias(c)
                if s is not None:
                    spans.append(s)
                break

    violaciones = [c.id for c in chunks if any(cumple(c, p) for p in prohibidos)]

    # PRECISION. Cuanta de la evidencia entregada es relevante. recall dice si el
    # hecho llego; precision dice cuanto ruido llego con el. El piso mete ~9
    # chunks pase lo que pase, asi que sin esta metrica el coste de esa garantia
    # no se ve en ningun numero.
    utiles = sum(1 for c in chunks if any(cumple(c, p) for p in esperados))
    precision = (utiles / len(chunks)) if chunks and esperados else None

    return {
        "id": caso["id"], "categoria": caso.get("categoria", "?"),
        "recall": recall, "rr": rr if esperados else None,
        "precision": precision,
        "satisfechos": satisfechos, "esperados": len(esperados),
        "spans": spans, "violaciones": violaciones,
    }


# --------------------------------------------------------------------------- #
# Corrida
# --------------------------------------------------------------------------- #
def correr(gold: dict, snapshot, embebedor, granularidad: str, k: int,
           rerank: bool = True, jerarquia: bool = False,
           grano_hijo: str = "semana") -> dict:
    t0 = time.time()
    rec = Recuperador.desde_snapshot(snapshot, embedder=embebedor,
                                     granularidad=granularidad, rerank=rerank,
                                     jerarquia=jerarquia, grano_hijo=grano_hijo)
    t_indice = time.time() - t0

    filas, t_consultas = [], 0.0
    for caso in gold["casos"]:
        t1 = time.time()
        ctx = rec.recuperar(caso["pregunta"], k=k)
        t_consultas += time.time() - t1
        # Se evalúa sobre el contexto COMPLETO (piso + recuperados): es lo que
        # de verdad llega al modelo. La columna 'solo recuperado' aísla la
        # calidad de la búsqueda de la garantía del piso.
        fila = evaluar_caso(caso, ctx.chunks()[:max(k, len(ctx.piso) + k)])
        # El MRR del contexto completo mide el orden del PISO, no el de la
        # busqueda: `Contexto.chunks()` devuelve `piso + recuperados`, y el piso
        # ocupa las primeras ~9 posiciones. Este `evaluar_caso` ya calculaba el
        # rr sin piso y se tiraba, quedandose solo con el recall.
        sin_piso = evaluar_caso(caso, [r.chunk for r in ctx.recuperados])
        fila["recall_sin_piso"] = sin_piso["recall"]
        fila["rr_sin_piso"] = sin_piso["rr"]
        fila["precision_sin_piso"] = sin_piso["precision"]
        fila["recall_solo_piso"] = evaluar_caso(caso, ctx.piso)["recall"]
        filas.append(fila)

    con_gold = [f for f in filas if f["recall"] is not None]
    n = len(con_gold) or 1
    spans = [s for f in filas for s in f["spans"]]
    return {
        "granularidad": granularidad,
        "rerank": rerank,
        "n_chunks": len(rec.chunks),
        "n_casos": len(filas),
        "n_casos_con_gold": len(con_gold),
        "recall": sum(f["recall"] for f in con_gold) / n,
        "recall_sin_piso": sum(f["recall_sin_piso"] or 0 for f in con_gold) / n,
        "recall_solo_piso": sum(f["recall_solo_piso"] or 0 for f in con_gold) / n,
        "mrr": sum(f["rr"] or 0 for f in con_gold) / n,
        "mrr_sin_piso": sum(f["rr_sin_piso"] or 0 for f in con_gold) / n,
        "precision": sum(f["precision"] or 0 for f in con_gold) / n,
        "precision_sin_piso": sum(f["precision_sin_piso"] or 0 for f in con_gold) / n,
        "perfectos": sum(1 for f in con_gold if f["recall"] == 1.0),
        "span_mediano": statistics.median(spans) if spans else None,
        "n_spans": len(spans),
        "violaciones": sum(len(f["violaciones"]) for f in filas),
        "t_indice": t_indice,
        "ms_consulta": 1000 * t_consultas / max(len(filas), 1),
        "filas": filas,
    }


def por_categoria(filas: list[dict]) -> dict[str, tuple[float, int]]:
    acc: dict[str, list[float]] = {}
    for f in filas:
        if f["recall"] is not None:
            acc.setdefault(f["categoria"], []).append(f["recall"])
    return {c: (sum(v) / len(v), len(v)) for c, v in sorted(acc.items())}


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #
def imprimir(res: dict, verboso: bool) -> None:
    print(f"\n--- granularidad={res['granularidad']}  "
          f"({res['n_chunks']} chunks) ---")
    n = res["n_casos_con_gold"]
    print(f"  recall@k              {res['recall']:.3f}   "
          f"({res['perfectos']}/{n} casos perfectos)")
    print(f"    solo lo recuperado  {res['recall_sin_piso']:.3f}   "
          f"(sin el piso determinista)")
    print(f"    solo el piso        {res['recall_solo_piso']:.3f}   "
          f"(sin la búsqueda)")
    print(f"  MRR                   {res['mrr']:.3f}   "
          f"(ordena el PISO: son las primeras ~9 posiciones)")
    print(f"    solo lo recuperado  {res['mrr_sin_piso']:.3f}   "
          f"<- el que mide la BUSQUEDA")
    print(f"  precision@k           {res['precision']:.3f}   "
          f"(fraccion del contexto que es relevante)")
    print(f"    solo lo recuperado  {res['precision_sin_piso']:.3f}")

    # Incertidumbre, y sobre todo: que mejora NO puede detectar esta muestra.
    # Va debajo de los puntos y no en una nota al pie porque es la lectura que
    # cambia la conclusion, no un anexo.
    print()
    print("  incertidumbre y poder de deteccion:")
    for etiqueta, valor in (("recall@k", res["recall"]),
                            ("recall recuperado", res["recall_sin_piso"]),
                            ("MRR recuperado", res["mrr_sin_piso"])):
        for linea in linea_mde(etiqueta, diagnostico(valor, n)):
            print(linea)
    if res["span_mediano"] is not None:
        print(f"  span de la evidencia  {res['span_mediano']:.0f} días "
              f"(mediana sobre {res['n_spans']} casos con fecha; menos es mejor)")
    print(f"  violaciones           {res['violaciones']}   "
          f"(chunks que NO debían aparecer)")
    print(f"  índice                {res['t_indice']:.1f} s   "
          f"consulta {res['ms_consulta']:.1f} ms")

    print("  por categoría:")
    for cat, (r, n) in por_categoria(res["filas"]).items():
        print(f"    {cat:<16} {r:.3f}  ({n} casos)")

    fallidos = [f for f in res["filas"]
                if f["recall"] is not None and f["recall"] < 1.0]
    if fallidos and verboso:
        print("  casos incompletos:")
        for f in fallidos:
            print(f"    {f['id']:<32} {f['satisfechos']}/{f['esperados']}")
    con_violacion = [f for f in res["filas"] if f["violaciones"]]
    if con_violacion:
        print("  VIOLACIONES:")
        for f in con_violacion:
            print(f"    {f['id']}: {f['violaciones']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluación de recuperación de Precio Vivo")
    ap.add_argument("--granularidad", default="semana",
                    choices=[*GRANULARIDADES, "todas"])
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--umbral", type=float, default=0.0,
                    help="recall@k mínimo; por debajo, sale con código 1")
    ap.add_argument("--embedder", default="fake",
                    choices=["fake", "local", "api", "bedrock", "auto"])
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--jerarquia", action="store_true",
                    help="busca en el grano de --granularidad y entrega el hijo")
    ap.add_argument("--grano-hijo", default="semana", choices=list(GRANULARIDADES))
    ap.add_argument("--sin-rerank", action="store_true",
                    help="desactiva el reordenamiento estructurado (para A/B)")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    ap.add_argument("-v", "--verboso", action="store_true")
    args = ap.parse_args()

    ruta = Path(args.snapshot)
    if not ruta.exists():
        print(f"No encuentro el snapshot en {ruta}. Genera uno con "
              f"`python -m preciovivo.export`.", file=sys.stderr)
        return 2

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    embebedor = get_embedder(args.embedder)
    granularidades = (list(GRANULARIDADES) if args.granularidad == "todas"
                      else [args.granularidad])

    if not args.json:
        print(f"== Evaluación de recuperación · {len(gold['casos'])} casos · "
              f"k={args.k} ==")
        print(f"embebedor: {embebedor.firma}")
        if args.embedder == "fake":
            print("  AVISO: el embebedor 'fake' mide MECÁNICA (filtros, piso, "
                  "fusión), no calidad semántica.")
            print("  Para medir calidad real: --embedder local  (o api).")
        print(f"snapshot: {ruta}")

    resultados = [correr(gold, str(ruta), embebedor, g, args.k,
                         rerank=not args.sin_rerank, jerarquia=args.jerarquia,
                         grano_hijo=args.grano_hijo)
                  for g in granularidades]

    if args.json:
        # stdout se codifica en cp1252 en Windows y `ensure_ascii=False`
        # perdia los acentos al redirigir: "pronostico" con tilde acababa como
        # U+FFFD y las comprobaciones por subcadena del gold set no encontraban
        # nada. El JSON seguia siendo valido, con el contenido roto.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps([{k: v for k, v in r.items() if k != "filas"}
                          for r in resultados], ensure_ascii=False, indent=2))
    else:
        for r in resultados:
            imprimir(r, args.verboso)
        if len(resultados) > 1:
            print("\n--- comparación de granularidad "
                  "(los números que cita el README) ---")
            print(f"  {'grano':<8} {'chunks':>8} {'recall@k':>10} "
                  f"{'rec.busq':>9} {'MRR':>8} {'MRR busq':>9} "
                  f"{'span(d)':>9} {'ms/consulta':>12}")
            for r in resultados:
                span = f"{r['span_mediano']:.0f}" if r["span_mediano"] is not None else "—"
                print(f"  {r['granularidad']:<8} {r['n_chunks']:>8} "
                      f"{r['recall']:>10.3f} {r['recall_sin_piso']:>9.3f} "
                      f"{r['mrr']:>8.3f} {r['mrr_sin_piso']:>9.3f} {span:>9} "
                      f"{r['ms_consulta']:>12.1f}")
            print("\n  recall/MRR miden si el chunk correcto SE ENCUENTRA.")
            print("  span mide cuántos días cubre la evidencia que se entrega:")
            print("  un chunk mensual satisface el predicado igual, pero le da al")
            print("  modelo ~20 días hábiles promediados en vez de ~5.")

    peor = min(r["recall"] for r in resultados)
    violaciones = sum(r["violaciones"] for r in resultados)
    if args.umbral > 0 and peor < args.umbral:
        print(f"\nFALLA: recall@k {peor:.3f} por debajo del umbral "
              f"{args.umbral:.3f}", file=sys.stderr)
        return 1
    if violaciones:
        print(f"\nFALLA: {violaciones} chunk(s) que no debían recuperarse",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
