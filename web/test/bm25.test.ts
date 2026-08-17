/**
 * BM25 + RRF del sitio — ESPEJO de `pipeline/tests/test_retrieval.py`.
 *
 * Cada caso de aquí tiene su gemelo en Python (mismo nombre traducido, mismas
 * aserciones). Son dos implementaciones de las mismas reglas y por eso divergen
 * si se toca una sola: estos tests son el detector de esa deriva. Si cambias
 * `retrieval.BM25` o `retrieval.rrf`, esto tiene que romperse.
 *
 * Por qué el sitio corre BM25 ahora: el embebido de la consulta depende de un
 * proveedor externo con cuota por minuto. Cuando esa cuota se agota, BM25 es lo
 * único que sigue recuperando — corre sobre el texto que ya viaja en el deploy.
 */
import { describe, expect, it } from "vitest";
import { buscarBM25, construirBM25, rrf, type RagChunk } from "@/lib/rag";

const RRF_C = 60; // espeja retrieval.RRF_C

function chunk(id: string, texto: string, slug: string | null = null): RagChunk {
  return { id, tipo: "producto-periodo", texto, slug, d0: "2026-01-05", d1: "2026-01-09" };
}

/** Corpus mínimo con la trampa del proyecto: "papa" vive dentro de "papaya". */
const CHUNKS: RagChunk[] = [
  chunk("p:papa-blanca:w1", "Papa Blanca · semana 2026-W02. Precio: abrió en S/ 2.00 por kg.", "papa-blanca"),
  chunk("p:papa-amarilla:w1", "Papa Amarilla · semana 2026-W02. Precio: abrió en S/ 3.50 por kg.", "papa-amarilla"),
  chunk("p:papaya:w1", "Papaya · semana 2026-W02. Precio: abrió en S/ 4.20 por kg.", "papaya"),
  chunk("p:tomate:w1", "Tomate · semana 2026-W02. Precio: abrió en S/ 3.00 por kg.", "tomate"),
  chunk("p:tomate:w2", "Tomate · semana 2026-W03. Anomalía detectada: el precio se desvió.", "tomate"),
];

describe("BM25", () => {
  it("prioriza el documento relevante", () => {
    const bm = construirBM25(CHUNKS);
    const res = buscarBM25(bm, CHUNKS, "papaya", 5);
    expect(res.length).toBeGreaterThan(0);
    expect(CHUNKS[res[0][0]].texto).toContain("Papaya");
  });

  it("no confunde 'papa' con 'papaya' (token completo, nunca subcadena)", () => {
    const bm = construirBM25(CHUNKS);
    const ids = buscarBM25(bm, CHUNKS, "papa", 5).map(([i]) => CHUNKS[i].id);
    expect(ids).toContain("p:papa-blanca:w1");
    expect(ids).not.toContain("p:papaya:w1");
  });

  it("respeta el conjunto permitido (pre-filtro antes del ranking)", () => {
    const bm = construirBM25(CHUNKS);
    const permitidos = new Set(
      CHUNKS.map((c, i) => (c.slug === "tomate" ? i : -1)).filter((i) => i >= 0),
    );
    const res = buscarBM25(bm, CHUNKS, "precio", 10, permitidos);
    expect(res.length).toBeGreaterThan(0);
    for (const [i] of res) expect(CHUNKS[i].slug).toBe("tomate");
  });

  it("sin coincidencias devuelve vacío", () => {
    const bm = construirBM25(CHUNKS);
    expect(buscarBM25(bm, CHUNKS, "xyzzyqwerty", 5)).toEqual([]);
  });

  it("es reproducible", () => {
    const bm = construirBM25(CHUNKS);
    const a = buscarBM25(bm, CHUNKS, "precio de la papa", 10);
    const b = buscarBM25(bm, CHUNKS, "precio de la papa", 10);
    expect(a).toEqual(b);
  });

  it("desempata por id de chunk, igual que el vectorial", () => {
    // Dos chunks con texto idéntico salvo el nombre: mismo score para un
    // término que comparten. Sin criterio explícito el orden sería el de
    // inserción y `recall@k` oscilaría entre corridas.
    const gemelos = [chunk("z:segundo", "Precio del periodo."), chunk("a:primero", "Precio del periodo.")];
    const bm = construirBM25(gemelos);
    const ids = buscarBM25(bm, gemelos, "precio", 2).map(([i]) => gemelos[i].id);
    expect(ids).toEqual(["a:primero", "z:segundo"]);
  });

  it("un corpus vacío no revienta", () => {
    const bm = construirBM25([]);
    expect(buscarBM25(bm, [], "papa", 5)).toEqual([]);
  });
});

describe("RRF", () => {
  it("premia estar arriba en ambas listas", () => {
    const f = rrf([
      ["a", "b", "c"],
      ["b", "a", "c"],
    ]);
    expect(f.get("b")!).toBeGreaterThan(f.get("c")!);
    expect(f.get("a")!).toBeGreaterThan(f.get("c")!);
  });

  it("suma las listas", () => {
    const f = rrf([["a"], ["a"]]);
    expect(f.get("a")!).toBeCloseTo(2 / (RRF_C + 1), 12);
  });

  it("con listas disjuntas, empata", () => {
    const f = rrf([["a"], ["b"]]);
    expect(f.get("a")!).toBeCloseTo(f.get("b")!, 12);
  });

  it("listas vacías dan fusión vacía", () => {
    expect(rrf([[], []]).size).toBe(0);
  });

  it("una sola lista degenera en ese ranking — el caso sin vectores", () => {
    // Es lo que ocurre cuando el proveedor de embeddings no está disponible:
    // la fusión recibe solo la lista léxica y debe conservar SU orden.
    const f = rrf([["a", "b", "c"]]);
    const orden = [...f.entries()].sort((x, y) => y[1] - x[1]).map(([id]) => id);
    expect(orden).toEqual(["a", "b", "c"]);
  });
});
