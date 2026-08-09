/**
 * Tests de la recuperación del sitio (lib/rag.ts).
 *
 * IMPORTANTE: los casos de `detectarProductos` y `detectarRangoFechas` ESPEJAN a
 * propósito los de `pipeline/tests/test_retrieval.py`. Son dos implementaciones
 * de las mismas reglas y ese es el punto donde pueden divergir; tener los mismos
 * casos fijados a ambos lados hace que la deriva rompa tests en vez de aparecer
 * como una respuesta rara en producción.
 *
 * Si tocas una regla, tócala en los dos lados y actualiza los dos tests.
 */
import { describe, expect, it } from "vitest";
import {
  acepta,
  aPrompt,
  buscar,
  detectarProductos,
  detectarRangoFechas,
  esEstacional,
  normalizar,
  piso,
  tokenizar,
  type Indice,
  type RagChunk,
} from "@/lib/rag";

const CATALOGO = {
  "papa-blanca": "Papa Blanca",
  "papa-amarilla": "Papa Amarilla",
  papaya: "Papaya",
  tomate: "Tomate",
  "ajo-morado": "Ajo Morado",
  "ajo-criollo-o-napuri": "Ajo Criollo O Napuri",
};

const REF = "2026-08-07"; // viernes

const chunk = (p: Partial<RagChunk> = {}): RagChunk => ({
  id: "x",
  tipo: "producto-periodo",
  texto: "t",
  slug: "papa-blanca",
  d0: "2026-08-03",
  d1: "2026-08-07",
  ...p,
});

describe("normalización", () => {
  it("quita acentos y baja a minúsculas", () => {
    expect(normalizar("MARACUYÁ")).toBe("maracuya");
    expect(normalizar("Ají Montaña")).toBe("aji montana");
  });

  it("tokeniza descartando separadores", () => {
    expect(tokenizar("Col China/Longapa")).toEqual(["col", "china", "longapa"]);
  });
});

describe("detectarProductos — espeja test_retrieval.py", () => {
  it("nombre completo", () => {
    expect(detectarProductos("¿cuánto está la papa blanca?", CATALOGO)).toEqual(
      new Set(["papa-blanca"]),
    );
  });

  it("la primera palabra agrupa variedades", () => {
    expect(detectarProductos("¿por qué subió la papa?", CATALOGO)).toEqual(
      new Set(["papa-blanca", "papa-amarilla"]),
    );
  });

  it("papa no matchea papaya", () => {
    expect(detectarProductos("precio de la papa", CATALOGO).has("papaya")).toBe(false);
    expect(detectarProductos("precio de la papaya", CATALOGO)).toEqual(new Set(["papaya"]));
  });

  it("la variedad explícita no arrastra a las demás", () => {
    expect(detectarProductos("¿cuánto está la papa blanca?", CATALOGO)).toEqual(
      new Set(["papa-blanca"]),
    );
  });

  it("dos variedades de la misma familia", () => {
    expect(detectarProductos("compara la papa blanca con la papa amarilla", CATALOGO)).toEqual(
      new Set(["papa-blanca", "papa-amarilla"]),
    );
  });

  it("nombre canónico parcial", () => {
    expect(detectarProductos("diferencia entre ajo morado y ajo criollo", CATALOGO)).toEqual(
      new Set(["ajo-morado", "ajo-criollo-o-napuri"]),
    );
  });

  it("sin acentos ni mayúsculas", () => {
    expect(detectarProductos("PRECIO DEL TOMATE", CATALOGO)).toEqual(new Set(["tomate"]));
  });

  it("sin producto", () => {
    expect(detectarProductos("¿qué está más barato hoy?", CATALOGO).size).toBe(0);
  });

  it("pregunta sin tokens", () => {
    expect(detectarProductos("¿?", CATALOGO).size).toBe(0);
  });
});

describe("detectarRangoFechas — espeja test_retrieval.py", () => {
  const casos: [string, string | undefined, string | undefined][] = [
    ["¿qué está más barato hoy?", REF, REF],
    ["¿cuánto costaba ayer?", "2026-08-06", "2026-08-06"],
    ["¿por qué subió esta semana?", "2026-08-03", REF],
    ["¿y la semana pasada?", "2026-07-27", "2026-08-02"],
    ["¿cómo estuvo este mes?", "2026-08-01", REF],
    ["¿y el mes pasado?", "2026-07-01", "2026-07-31"],
    ["¿qué pasó en julio?", "2026-07-01", "2026-07-31"],
    ["¿qué pasó en diciembre?", "2025-12-01", "2025-12-31"],
    ["¿qué pasó en marzo de 2025?", "2025-03-01", "2025-03-31"],
    ["el 2026-01-15", "2026-01-15", "2026-01-15"],
    ["en los últimos 30 días", "2026-07-08", REF],
    ["¿cómo fue el año pasado?", "2025-01-01", "2025-12-31"],
    ["¿cómo estuvo el mercado el 15 de julio?", "2026-07-15", "2026-07-15"],
    ["el 3 de marzo de 2025", "2025-03-03", "2025-03-03"],
    ["el 20 de diciembre", "2025-12-20", "2025-12-20"],
  ];
  it.each(casos)("%s", (pregunta, desde, hasta) => {
    expect(detectarRangoFechas(pregunta, REF)).toEqual({ desde, hasta });
  });

  it("sin expresión temporal no filtra", () => {
    expect(detectarRangoFechas("¿cuánto cuesta la papa?", REF)).toEqual({});
  });

  it("sin fecha de referencia no filtra", () => {
    expect(detectarRangoFechas("esta semana", null)).toEqual({});
  });

  it("la pregunta estacional no se ancla a un año", () => {
    expect(esEstacional("¿cuánto suele costar la zanahoria en agosto?")).toBe(true);
    expect(detectarRangoFechas("¿cuánto suele costar la zanahoria en agosto?", REF)).toEqual({});
    expect(detectarRangoFechas("¿cuánto costó en agosto?", REF)).not.toEqual({});
  });

  it("fecha imposible cae al mes", () => {
    expect(detectarRangoFechas("el 31 de febrero de 2026", REF)).toEqual({
      desde: "2026-02-01",
      hasta: "2026-02-28",
    });
  });
});

describe("filtro por solapamiento", () => {
  it("un chunk cubre un rango, no un punto", () => {
    const c = chunk();
    expect(acepta(c, { desde: "2026-08-05", hasta: "2026-08-05" })).toBe(true);
    expect(acepta(c, { desde: "2026-08-07" })).toBe(true);
    expect(acepta(c, { hasta: "2026-08-03" })).toBe(true);
    expect(acepta(c, { desde: "2026-08-08" })).toBe(false);
    expect(acepta(c, { hasta: "2026-08-02" })).toBe(false);
  });

  it("filtra por slug", () => {
    expect(acepta(chunk(), { slugs: new Set(["papa-blanca"]) })).toBe(true);
    expect(acepta(chunk(), { slugs: new Set(["tomate"]) })).toBe(false);
    expect(acepta(chunk({ slug: null }), { slugs: new Set(["tomate"]) })).toBe(false);
  });
});

describe("búsqueda vectorial", () => {
  const dims = 2;
  const chunks: RagChunk[] = [
    chunk({ id: "a", slug: "papa-blanca" }),
    chunk({ id: "b", slug: "tomate" }),
    chunk({ id: "c", slug: "tomate" }),
  ];
  const idx: Indice = {
    chunks,
    // a=(1,0)  b=(0,1)  c=(1,0) -> a y c empatan contra la consulta (1,0)
    vectores: new Float32Array([1, 0, 0, 1, 1, 0]),
    dims,
    firmaEmbedder: "fake:test:2",
    granularidad: "semana",
  };

  it("ordena por similitud", () => {
    const r = buscar(idx, new Float32Array([1, 0]), 3);
    expect(r[0].score).toBeCloseTo(1);
    expect(r[2].chunk.id).toBe("b");
  });

  it("desempata por id, igual que el pipeline", () => {
    const r = buscar(idx, new Float32Array([1, 0]), 2);
    expect(r.map((x) => x.chunk.id)).toEqual(["a", "c"]);
  });

  it("aplica el filtro antes del k-NN", () => {
    const r = buscar(idx, new Float32Array([1, 0]), 5, { slugs: new Set(["tomate"]) });
    expect(r.map((x) => x.chunk.id)).toEqual(["c", "b"]);
  });
});

describe("piso determinista", () => {
  const chunks: RagChunk[] = [
    chunk({ id: "perfil-papa", tipo: "producto-perfil", slug: "papa-blanca", d0: "2024-07-01" }),
    chunk({ id: "sem-papa-32", slug: "papa-blanca", d0: "2026-08-03", d1: "2026-08-07" }),
    chunk({ id: "sem-papa-31", slug: "papa-blanca", d0: "2026-07-27", d1: "2026-07-31" }),
    chunk({ id: "anom-papa", tipo: "evento-anomalia", slug: "papa-blanca", d0: "2026-05-04", d1: "2026-05-04" }),
    chunk({ id: "dia-07", tipo: "mercado-dia", slug: null, d0: "2026-08-07", d1: "2026-08-07" }),
    chunk({ id: "dia-06", tipo: "mercado-dia", slug: null, d0: "2026-08-06", d1: "2026-08-06" }),
  ];
  const idx: Indice = {
    chunks,
    vectores: new Float32Array(chunks.length * 2),
    dims: 2,
    firmaEmbedder: "fake:test:2",
    granularidad: "semana",
  };

  it("un producto nombrado siempre trae contexto", () => {
    expect(piso(idx, new Set(["papa-blanca"])).length).toBeGreaterThan(0);
  });

  it("sin fecha, el perfil va primero", () => {
    expect(piso(idx, new Set(["papa-blanca"]))[0].id).toBe("perfil-papa");
  });

  it("con fecha, la ventana pedida va primero", () => {
    const r = piso(idx, new Set(["papa-blanca"]), "2026-08-03", "2026-08-07");
    expect(r[0].id).toBe("sem-papa-32");
  });

  it("pregunta agregada trae el resumen del día", () => {
    const r = piso(idx, new Set());
    expect(r.every((c) => c.tipo === "mercado-dia")).toBe(true);
    expect(r[0].id).toBe("dia-07");
  });

  it("respeta el presupuesto con muchos productos", () => {
    const muchos = new Set(["papa-blanca", "tomate", "papaya"]);
    expect(piso(idx, muchos).length).toBeLessThanOrEqual(14);
  });
});

describe("serialización del prompt", () => {
  it("separa lo garantizado de lo recuperado", () => {
    const p = aPrompt({
      piso: [chunk({ id: "a", texto: "hecho garantizado" })],
      recuperados: [{ chunk: chunk({ id: "b", texto: "hecho recuperado" }), score: 0.5 }],
      slugs: new Set(["papa-blanca"]),
    });
    expect(p).toContain("productos que menciona la pregunta");
    expect(p).toContain("CONTEXTO RECUPERADO");
    expect(p.indexOf("garantizado")).toBeLessThan(p.indexOf("recuperado"));
  });

  it("describe el piso según lo que contiene", () => {
    const p = aPrompt({
      piso: [chunk({ id: "d", tipo: "mercado-dia", slug: null, texto: "resumen" })],
      recuperados: [],
      slugs: new Set(),
    });
    expect(p).toContain("resumen del mercado");
    expect(p).not.toContain("productos que menciona la pregunta");
  });

  it("no repite un chunk que ya está en el piso", () => {
    const c = chunk({ id: "a", texto: "unico" });
    const p = aPrompt({ piso: [c], recuperados: [{ chunk: c, score: 1 }], slugs: new Set(["papa-blanca"]) });
    expect(p.match(/unico/g)?.length).toBe(1);
  });
});
