/**
 * CONTRATO DE CONFORMIDAD con la implementación de referencia (Python).
 *
 * El fixture lo genera `pipeline/tests/conformidad.py` desde `retrieval.py`, que
 * es la implementación que usan las evaluaciones, la CLI y la API. Este archivo
 * exige que `web/lib/rag.ts` produzca EXACTAMENTE lo mismo.
 *
 * Por qué existe: el sitio reimplementa el parseo de la pregunta y BM25 porque
 * no tiene a quién preguntarle. La garantía de que las dos implementaciones no
 * divergen era hasta ahora CONVENCIONAL —espejar cada test a mano— y una
 * convención no detecta lo que nadie se acordó de escribir.
 *
 * El ciclo: si cambia `retrieval.py`, `pipeline/tests/test_conformidad.py` falla
 * hasta que se regenere el fixture; regenerarlo hace fallar ESTE archivo hasta
 * que `rag.ts` alcance a Python. La deriva deja de ser posible en silencio.
 *
 * Si un caso falla, la respuesta correcta casi nunca es cambiar el fixture:
 * Python es la referencia.
 */
import { describe, expect, it } from "vitest";
import {
  buscarBM25,
  construirBM25,
  detectarProductos,
  detectarRangoFechas,
  esEstacional,
  rrf,
  tokenizar,
  type RagChunk,
} from "@/lib/rag";
import fixture from "./fixtures/conformidad.json";

type CasoParseo = {
  pregunta: string;
  tokens: string[];
  slugs: string[];
  desde: string | null;
  hasta: string | null;
  estacional: boolean;
};

const { catalogo, fecha_ref: fechaRef, rrf_c: rrfC } = fixture;

describe("conformidad · parseo de la pregunta", () => {
  it("el fixture trae los casos difíciles del dominio", () => {
    // Un contrato sin casos difíciles no contrata nada.
    expect(fixture.parseo.length).toBeGreaterThanOrEqual(20);
    const preguntas = fixture.parseo.map((c) => c.pregunta);
    expect(preguntas).toContain("¿a cuánto está la papa?");
    expect(preguntas).toContain("¿y la papaya?");
    expect(preguntas).toContain("¿cuánto suele costar la zanahoria en agosto?");
  });

  it.each(fixture.parseo as CasoParseo[])(
    "detectarProductos coincide con Python · %#",
    (caso) => {
      const obtenido = [...detectarProductos(caso.pregunta, catalogo)].sort();
      expect(obtenido, `pregunta: ${caso.pregunta}`).toEqual(caso.slugs);
    },
  );

  it.each(fixture.parseo as CasoParseo[])(
    "detectarRangoFechas coincide con Python · %#",
    (caso) => {
      const { desde, hasta } = detectarRangoFechas(caso.pregunta, fechaRef);
      expect([desde ?? null, hasta ?? null], `pregunta: ${caso.pregunta}`).toEqual([
        caso.desde,
        caso.hasta,
      ]);
    },
  );

  it.each(fixture.parseo as CasoParseo[])(
    "esEstacional y tokenizar coinciden con Python · %#",
    (caso) => {
      expect(esEstacional(caso.pregunta), `pregunta: ${caso.pregunta}`).toBe(
        caso.estacional,
      );
      expect(tokenizar(caso.pregunta), `pregunta: ${caso.pregunta}`).toEqual(caso.tokens);
    },
  );
});

describe("conformidad · BM25", () => {
  const chunks: RagChunk[] = fixture.corpus.map((c) => ({
    id: c.id,
    tipo: c.id.split(":")[0] as RagChunk["tipo"],
    texto: c.texto,
    slug: null,
    d0: "2026-08-03",
    d1: "2026-08-07",
  }));
  const bm = construirBM25(chunks);

  it.each(fixture.bm25)("el ranking coincide con Python · $pregunta", (caso) => {
    // Se compara el ORDEN de ids, no los scores: ambos lenguajes usan float64
    // pero acumulan en distinto orden, y dos scores separados por 1e-16 no
    // significan nada. El desempate por id —que las dos implementaciones
    // aplican— es lo que hace el orden total y comparable.
    const ids = buscarBM25(bm, chunks, caso.pregunta, chunks.length).map(
      ([i]) => chunks[i].id,
    );
    expect(ids).toEqual(caso.ids);
  });
});

describe("conformidad · RRF", () => {
  it("usa la misma constante C que Python", () => {
    const f = rrf([["a"]]);
    expect(f.get("a")!).toBeCloseTo(1 / (rrfC + 1), 12);
  });

  it.each(fixture.rrf)("la fusión coincide con Python · %#", (caso) => {
    const f = rrf(caso.listas);
    const orden = [...f.entries()]
      .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
      .map(([id]) => id);
    expect(orden).toEqual(caso.orden);
    for (const [id, s] of Object.entries(caso.scores)) {
      expect(f.get(id)!, `score de ${id}`).toBeCloseTo(s as number, 9);
    }
  });
});
