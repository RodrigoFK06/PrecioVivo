import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";
import Anthropic from "@anthropic-ai/sdk";
import { buscarProductos, type Producto, type Snapshot } from "@/lib/data";

export const dynamic = "force-dynamic";

/** Fila devuelta al cliente. Solo precios REALES del snapshot, nunca inventados. */
type Fila = {
  nombre: string;
  slug: string;
  precio_kg: number | null;
  var_pct: number | null;
};

type Respuesta = {
  texto: string;
  productos: Fila[];
  fuente?: "claude" | "fallback";
};

const fila = (p: Producto): Fila => ({
  nombre: p.nombre,
  slug: p.slug,
  precio_kg: p.latest.precio_kg,
  var_pct: p.latest.var_pct,
});

// Carga el snapshot directamente (server-side). No depende de getSnapshot para
// evitar cualquier acoplamiento; es la misma fuente (data/snapshot.json).
async function cargarSnapshot(): Promise<Snapshot> {
  const p = path.join(process.cwd(), "data", "snapshot.json");
  const raw = await fs.readFile(p, "utf-8");
  return JSON.parse(raw) as Snapshot;
}

const soles = (n: number | null) => (n == null ? "—" : `S/ ${n.toFixed(2)}`);
const pct = (n: number | null) => (n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`);

// ---------------------------------------------------------------------------
// FALLBACK por palabras clave (sin API key). Solo usa datos del snapshot.
// ---------------------------------------------------------------------------
function conPrecio(productos: Producto[]): Producto[] {
  return productos.filter((p) => p.latest.precio_kg != null);
}

function masBaratos(productos: Producto[], n = 6): Producto[] {
  return conPrecio(productos)
    .slice()
    .sort((a, b) => a.latest.precio_kg! - b.latest.precio_kg!)
    .slice(0, n);
}

function masCaros(productos: Producto[], n = 6): Producto[] {
  return conPrecio(productos)
    .slice()
    .sort((a, b) => b.latest.precio_kg! - a.latest.precio_kg!)
    .slice(0, n);
}

function masSubieron(productos: Producto[], n = 6): Producto[] {
  return productos
    .filter((p) => p.latest.var_pct != null)
    .slice()
    .sort((a, b) => b.latest.var_pct! - a.latest.var_pct!)
    .slice(0, n);
}

function masBajaron(productos: Producto[], n = 6): Producto[] {
  return productos
    .filter((p) => p.latest.var_pct != null)
    .slice()
    .sort((a, b) => a.latest.var_pct! - b.latest.var_pct!)
    .slice(0, n);
}

function fallback(q: string, snap: Snapshot): Respuesta {
  const productos = snap.productos;
  const norm = q.toLowerCase();

  // Intención por palabras clave (orden importa: "más barato" antes que nombre).
  if (/m[aá]s\s+barat|menor precio|m[aá]s econ[oó]mic|barat/.test(norm)) {
    const r = masBaratos(productos);
    const top = r[0];
    return {
      texto: top
        ? `Lo más barato hoy es ${top.nombre} a ${soles(top.latest.precio_kg)}/kg. Aquí los ${r.length} más baratos del mercado.`
        : "No hay precios disponibles en el snapshot.",
      productos: r.map(fila),
      fuente: "fallback",
    };
  }

  if (/m[aá]s\s+car|mayor precio|car[oa]s?/.test(norm)) {
    const r = masCaros(productos);
    const top = r[0];
    return {
      texto: top
        ? `Lo más caro hoy es ${top.nombre} a ${soles(top.latest.precio_kg)}/kg. Aquí los ${r.length} más caros del mercado.`
        : "No hay precios disponibles en el snapshot.",
      productos: r.map(fila),
      fuente: "fallback",
    };
  }

  if (/sub[ie]|encarec|alza|aument/.test(norm)) {
    const r = masSubieron(productos);
    const top = r[0];
    return {
      texto: top
        ? `Lo que más subió respecto a ayer es ${top.nombre} (${pct(top.latest.var_pct)}, ahora ${soles(top.latest.precio_kg)}/kg).`
        : "No hay variaciones registradas en el snapshot.",
      productos: r.map(fila),
      fuente: "fallback",
    };
  }

  if (/baj[oó]|baj[ae]|cay[oó]|cae|descend|abarat/.test(norm)) {
    const r = masBajaron(productos);
    const top = r[0];
    return {
      texto: top
        ? `Lo que más bajó respecto a ayer es ${top.nombre} (${pct(top.latest.var_pct)}, ahora ${soles(top.latest.precio_kg)}/kg).`
        : "No hay variaciones registradas en el snapshot.",
      productos: r.map(fila),
      fuente: "fallback",
    };
  }

  // Búsqueda por nombre de producto (pura, segura).
  const encontrados = buscarProductos(productos, q);
  if (q.trim() && encontrados.length && encontrados.length < productos.length) {
    const r = encontrados.slice(0, 8);
    const lista =
      r.length === 1
        ? `${r[0].nombre} está a ${soles(r[0].latest.precio_kg)}/kg (${pct(r[0].latest.var_pct)} vs ayer).`
        : `Encontré ${r.length} productos que coinciden con "${q.trim()}".`;
    return {
      texto: lista,
      productos: r.map(fila),
      fuente: "fallback",
    };
  }

  // Sin intención reconocida: orientación honesta.
  return {
    texto:
      'Puedes preguntar por: "qué está más barato", "qué subió", "qué bajó" o el nombre de un producto (p. ej. "maracuyá"). Los precios son del último día del Gran Mercado Mayorista de Lima.',
    productos: [],
    fuente: "fallback",
  };
}

// ---------------------------------------------------------------------------
// CLAUDE (con API key). Interpreta la pregunta y elige productos del snapshot.
// El modelo SOLO puede citar productos por slug; el precio lo ponemos NOSOTROS
// desde el snapshot, así nunca se inventan cifras.
// ---------------------------------------------------------------------------
async function conClaude(q: string, snap: Snapshot): Promise<Respuesta> {
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  // Catálogo compacto para el contexto: hechos numéricos, no documentos fuente.
  const catalogo = snap.productos.map((p) => ({
    slug: p.slug,
    nombre: p.nombre,
    precio_kg: p.latest.precio_kg,
    var_pct: p.latest.var_pct,
    tendencia: p.latest.tendencia,
  }));

  const tool: Anthropic.Tool = {
    name: "responder",
    description:
      "Responde la pregunta del usuario sobre precios mayoristas. Devuelve una frase en español y los slugs de los productos relevantes (en orden de relevancia). Usa SOLO slugs presentes en el catálogo.",
    input_schema: {
      type: "object",
      properties: {
        texto: {
          type: "string",
          description:
            "Respuesta breve en español, citando productos por nombre. No inventes precios; si mencionas cifras usa exactamente las del catálogo.",
        },
        slugs: {
          type: "array",
          items: { type: "string" },
          description: "Slugs de los productos relevantes, de más a menos relevante. Máximo 8.",
        },
      },
      required: ["texto", "slugs"],
    },
  };

  const sistema = [
    "Eres el asistente de Precio Vivo, sobre precios mayoristas del Gran Mercado Mayorista de Lima.",
    "Responde SIEMPRE en español, claro y conciso, sin emojis.",
    "NUNCA inventes precios ni productos: usa únicamente el catálogo JSON que se te entrega.",
    "Precios en soles (S/) por kilogramo. var_pct es la variación vs. el día anterior.",
    "Para comparativos (más caro/barato, subió/bajó) ordena usando el catálogo y devuelve los slugs.",
    "Si la pregunta no se puede responder con los datos, dilo honestamente.",
    "Devuelve tu respuesta SIEMPRE llamando a la herramienta 'responder'.",
  ].join(" ");

  const msg = await client.messages.create({
    model: "claude-opus-4-8",
    max_tokens: 1024,
    system: sistema,
    tools: [tool],
    tool_choice: { type: "tool", name: "responder" },
    messages: [
      {
        role: "user",
        content: `Catálogo (JSON):\n${JSON.stringify(catalogo)}\n\nPregunta del usuario: ${q}`,
      },
    ],
  });

  // Extrae el tool_use.
  const bloque = msg.content.find((b) => b.type === "tool_use");
  if (!bloque || bloque.type !== "tool_use") {
    // El modelo no usó la herramienta: degradamos al fallback.
    return fallback(q, snap);
  }

  const input = bloque.input as { texto?: unknown; slugs?: unknown };
  const texto = typeof input.texto === "string" ? input.texto : "";
  const slugs = Array.isArray(input.slugs)
    ? input.slugs.filter((s): s is string => typeof s === "string")
    : [];

  // Mapea slugs -> productos REALES del snapshot. Filtra slugs inexistentes
  // (el precio sale del snapshot, nunca del modelo).
  const porSlug = new Map(snap.productos.map((p) => [p.slug, p]));
  const productos: Fila[] = [];
  for (const s of slugs.slice(0, 8)) {
    const p = porSlug.get(s);
    if (p) productos.push(fila(p));
  }

  if (!texto && productos.length === 0) return fallback(q, snap);

  return {
    texto: texto || "Aquí tienes los productos relevantes.",
    productos,
    fuente: "claude",
  };
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------
export async function POST(request: Request) {
  let q = "";
  try {
    const body = (await request.json()) as { q?: unknown };
    q = typeof body.q === "string" ? body.q : "";
  } catch {
    return NextResponse.json(
      { texto: "No se pudo leer la pregunta.", productos: [] } satisfies Respuesta,
      { status: 400 },
    );
  }

  if (!q.trim()) {
    return NextResponse.json({
      texto: 'Escribe una pregunta, p. ej. "¿qué está más barato hoy?".',
      productos: [],
    } satisfies Respuesta);
  }

  let snap: Snapshot;
  try {
    snap = await cargarSnapshot();
  } catch {
    return NextResponse.json(
      { texto: "No se pudieron cargar los datos del mercado.", productos: [] } satisfies Respuesta,
      { status: 500 },
    );
  }

  try {
    const r = process.env.ANTHROPIC_API_KEY
      ? await conClaude(q, snap)
      : fallback(q, snap);
    return NextResponse.json(r satisfies Respuesta);
  } catch {
    // Si Claude falla (red, cuota, etc.), degradamos al fallback con gracia.
    return NextResponse.json(fallback(q, snap) satisfies Respuesta);
  }
}
