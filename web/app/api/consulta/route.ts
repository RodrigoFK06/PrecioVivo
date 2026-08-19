import { NextResponse } from "next/server";
import OpenAI from "openai";
import { buscarProductos, getSnapshot, type Producto, type Snapshot } from "@/lib/data";
import { recuperarViaApi } from "@/lib/api";
import { aPrompt, recuperar } from "@/lib/rag";

// Proveedor LLM agnóstico (OpenAI-compatible). Por defecto DeepSeek (barato).
// Sin clave => modo fallback por palabras clave. Local/gratis: AI_BASE_URL=
// http://localhost:11434/v1 + AI_MODEL=llama3.1 (Ollama).
const AI_BASE_URL = process.env.AI_BASE_URL ?? "https://api.deepseek.com";
const AI_MODEL = process.env.AI_MODEL ?? "deepseek-chat";
const AI_API_KEY = process.env.AI_API_KEY ?? process.env.DEEPSEEK_API_KEY;

export const dynamic = "force-dynamic";

/** Fila devuelta al cliente. Solo precios REALES del snapshot, nunca inventados. */
type Fila = {
  nombre: string;
  slug: string;
  precio_kg: number | null;
  var_pct: number | null;
  /** Presente solo en productos de otros mercados (sin página /p/[slug]). */
  mercado?: string;
};

type Respuesta = {
  texto: string;
  productos: Fila[];
  /**
   * Con qué evidencia respondió el modelo. Los cuatro valores son distintos y
   * NO son intercambiables:
   *
   *   'llm-rag'         híbrido completo: BM25 + vectores + piso determinista.
   *   'llm-rag-lexico'  BM25 + piso, SIN vectores. Pasa cuando el proveedor de
   *                     embeddings falla o agota su cuota. Sigue siendo
   *                     recuperación real sobre el histórico: BM25 corre sobre
   *                     el texto que viaja en el deploy y no depende de nadie.
   *   'llm'             el catálogo del día, una línea por producto, sin
   *                     profundidad temporal.
   *   'fallback'        palabras clave, sin modelo.
   *
   * El peldaño intermedio existe porque antes ese caso se reportaba como
   * 'llm-rag': la búsqueda no corría y la respuesta salía igual, diciendo que
   * sí. Una degradación que no se declara es indistinguible de que todo
   * funcione.
   */
  fuente?: "llm" | "llm-rag" | "llm-rag-lexico" | "fallback";
  /**
   * QUÉ implementación recuperó el contexto, aparte de con cuánta evidencia.
   *
   *   'api'    el pipeline vía `POST /recuperar` — la MISMA implementación que
   *            usan las evaluaciones, la CLI y el MCP.
   *   'local'  el port de TypeScript de `web/lib/rag.ts`.
   *
   * Va en un campo aparte de `fuente` a propósito: son dos ejes distintos
   * (cuánta evidencia vs. quién la recuperó) y mezclarlos haría que el enum
   * creciera multiplicándose sin decir más.
   */
  motor?: "api" | "local";
  /** Por qué se degradó, si se degradó. Para poder depurar una respuesta pobre. */
  degradado?: string;
};

const fila = (p: Producto): Fila => ({
  nombre: p.nombre,
  slug: p.slug,
  precio_kg: p.latest.precio_kg,
  var_pct: p.latest.var_pct,
});

// Productos de OTROS mercados (pollo vivo vía SISAP, frutas del boletín):
// también son datos reales del snapshot y la consulta debe conocerlos — si no,
// "precio del pollo" respondería que no hay aves cuando sí las mostramos.
function extrasDe(snap: Snapshot): (Fila & { mercado: string })[] {
  return (snap.mercados ?? []).flatMap((m) =>
    m.productos
      .filter((p) => p.precio_kg != null)
      .map((p) => ({
        nombre: p.nombre,
        slug: p.slug,
        precio_kg: p.precio_kg,
        var_pct: p.var_pct,
        mercado: m.nombre,
      })),
  );
}

// Se usa `getSnapshot` de lib/data en vez de releer el archivo por nuestra
// cuenta. La copia anterior existía "para evitar acoplamiento", pero el efecto
// real era parsear 3,4 MB de JSON en CADA request de una ruta force-dynamic —y
// mantener dos lectores del mismo archivo que podían divergir. Es la misma
// fuente; compartir el lector memoizado es estrictamente mejor.
const cargarSnapshot = getSnapshot;

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

  // Búsqueda en otros mercados (pollo vivo del Mercado de Aves, frutas MMF2).
  const norm2 = (s: string) =>
    s
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .trim();
  const ex = extrasDe(snap).filter((e) => norm2(e.nombre).includes(norm2(q)));
  if (q.trim() && ex.length) {
    const r = ex.slice(0, 8);
    return {
      texto:
        r.length === 1
          ? `${r[0].nombre} está a ${soles(r[0].precio_kg)}/kg en el ${r[0].mercado} (${pct(r[0].var_pct)} vs ayer).`
          : `Encontré ${r.length} productos en otros mercados que coinciden con "${q.trim()}".`,
      productos: r,
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
// LLM (OpenAI-compatible, DeepSeek por defecto). Interpreta la pregunta y elige
// productos del snapshot. El modelo SOLO cita productos por slug; el precio lo
// ponemos NOSOTROS desde el snapshot, así nunca se inventan cifras.
// ---------------------------------------------------------------------------
async function conLLM(q: string, snap: Snapshot): Promise<Respuesta> {
  const client = new OpenAI({ apiKey: AI_API_KEY, baseURL: AI_BASE_URL });

  // Catálogo compacto para el contexto: hechos numéricos, no documentos fuente.
  // Incluye los productos de otros mercados (pollo vivo, frutas) con su mercado.
  const catalogo = [
    ...snap.productos.map((p) => ({
      slug: p.slug,
      nombre: p.nombre,
      precio_kg: p.latest.precio_kg,
      var_pct: p.latest.var_pct,
      tendencia: p.latest.tendencia,
      pronostico_kg: p.forecast?.precio_estimado ?? null,
      mercado: "GMML",
    })),
    ...extrasDe(snap).map((e) => ({
      slug: e.slug,
      nombre: e.nombre,
      precio_kg: e.precio_kg,
      var_pct: e.var_pct,
      tendencia: null as string | null,
      pronostico_kg: null as number | null,
      mercado: e.mercado,
    })),
  ];

  // Contexto de verificación: el asistente debe poder responder "¿esto está
  // corroborado?" con el cross-check SISAP del día, no con un "no sé".
  const v = snap.verificacion;
  let contexto = "";
  if (v && v.contrastados > 0) {
    contexto =
      `\n\nContexto verificable:\nLos precios GMML se contrastan a diario contra ` +
      `SISAP-MIDAGRI (publicación independiente). Último contraste (${v.fecha}): ` +
      `${v.coinciden}/${v.contrastados} coinciden dentro de ±${v.umbral_pct}%.` +
      (v.gmml_disponible ? "" : " (El contraste de ese día quedó pendiente de completar.)");
  }

  const tool = {
    type: "function" as const,
    function: {
      name: "responder",
      description:
        "Responde la pregunta del usuario sobre precios mayoristas. Devuelve una frase en español y los slugs de los productos relevantes (en orden de relevancia). Usa SOLO slugs presentes en el catálogo.",
      parameters: {
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
    },
  };

  const sistema = [
    "Eres el asistente de Precio Vivo, sobre precios mayoristas de los mercados de Lima.",
    "La mayoría del catálogo es del Gran Mercado Mayorista de Lima (mercado 'GMML'), pero",
    "también incluye productos de otros mercados (campo 'mercado'), como el pollo vivo",
    "del Mercado de Aves; al citarlos menciona su mercado.",
    "Responde SIEMPRE en español, claro y conciso, sin emojis.",
    "NUNCA inventes precios ni productos: usa únicamente el catálogo JSON que se te entrega.",
    "Precios en soles (S/) por kilogramo. var_pct es la variación vs. el día anterior.",
    "pronostico_kg es la predicción del modelo (IA, beta) para el siguiente día hábil;",
    "null = producto sin pronóstico todavía (p. ej. series recién incorporadas).",
    "Para comparativos (más caro/barato, subió/bajó) ordena usando el catálogo y devuelve los slugs.",
    "Si la pregunta no se puede responder con los datos, dilo honestamente.",
    "Devuelve tu respuesta SIEMPRE llamando a la herramienta 'responder'.",
  ].join(" ");

  const resp = await client.chat.completions.create({
    model: AI_MODEL,
    max_tokens: 1024,
    tools: [tool],
    tool_choice: { type: "function", function: { name: "responder" } },
    messages: [
      { role: "system", content: sistema },
      {
        role: "user",
        content: `Catálogo (JSON):\n${JSON.stringify(catalogo)}${contexto}\n\nPregunta del usuario: ${q}`,
      },
    ],
  });

  // Extrae la function call.
  const call = resp.choices[0]?.message?.tool_calls?.[0];
  if (!call || call.type !== "function") {
    // El modelo no usó la herramienta: degradamos al fallback.
    return fallback(q, snap);
  }

  let input: { texto?: unknown; slugs?: unknown };
  try {
    input = JSON.parse(call.function.arguments) as { texto?: unknown; slugs?: unknown };
  } catch {
    return fallback(q, snap);
  }
  const texto = typeof input.texto === "string" ? input.texto : "";
  const slugs = Array.isArray(input.slugs)
    ? input.slugs.filter((s): s is string => typeof s === "string")
    : [];

  // Mapea slugs -> productos REALES del snapshot. Filtra slugs inexistentes
  // (el precio sale del snapshot, nunca del modelo).
  const porSlug = new Map(snap.productos.map((p) => [p.slug, p]));
  const porSlugExtra = new Map(extrasDe(snap).map((e) => [e.slug, e]));
  const productos: Fila[] = [];
  for (const s of slugs.slice(0, 8)) {
    const p = porSlug.get(s);
    if (p) {
      productos.push(fila(p));
      continue;
    }
    const e = porSlugExtra.get(s);
    if (e) productos.push(e);
  }

  if (!texto && productos.length === 0) return fallback(q, snap);

  return {
    texto: texto || "Aquí tienes los productos relevantes.",
    productos,
    fuente: "llm",
  };
}

// ---------------------------------------------------------------------------
// RAG: el modelo responde sobre CONTEXTO RECUPERADO, no sobre el catálogo.
//
// La diferencia práctica: conLLM le manda una línea por producto (precio de hoy,
// variación vs ayer). Con eso no se puede contestar "¿por qué subió la papa esta
// semana?" — la evidencia temporal no está en el prompt. Aquí el modelo recibe
// las ventanas semanales, las anomalías detectadas y la ficha del producto.
// ---------------------------------------------------------------------------
/**
 * Contexto para el prompt: del pipeline si su API está configurada, del port de
 * TypeScript si no.
 *
 * El orden no es casual. La API es la implementación de REFERENCIA —la que usan
 * las evaluaciones, la CLI y el MCP— e incluye lo que el port no cubre. El
 * respaldo local existe para que el sitio siga siendo estático + snapshot: si la
 * API no está o no responde, se contesta igual.
 */
async function contextoDe(
  q: string,
  snap: Snapshot,
): Promise<{ contexto: string; motor: "api" | "local"; degradado?: string } | null> {
  const remoto = await recuperarViaApi(q);
  if (remoto) return { contexto: remoto.prompt, motor: "api" };

  // Los extras van en el catálogo del RAG, no solo en el del peldaño de abajo.
  // El comentario de `extrasDe` ya lo advertía y el peldaño de catálogo lo
  // cumplía; el del RAG no, así que al mejorar la recuperación esta clase de
  // pregunta EMPEORÓ: el peldaño bueno gana y nunca cede al que tenía el dato.
  // Medido en producción: "¿cómo va el pollo vivo?" respondía que no lo
  // seguimos, con el precio en el mismo JSON. Espejo de `retrieval.catalogo_de`.
  const catalogo = Object.fromEntries([
    ...snap.productos.map((p) => [p.slug, p.nombre]),
    ...extrasDe(snap).map((p) => [p.slug, p.nombre]),
  ]);
  const ctx = await recuperar(q, catalogo, snap.latestFecha ?? null);
  // Sin contexto recuperado no hay nada que RAG aporte sobre el camino normal.
  if (ctx.degradado && !ctx.piso.length) return null;
  const contexto = aPrompt(ctx);
  if (!contexto) return null;
  return { contexto, motor: "local", degradado: ctx.degradado };
}

async function conRAG(q: string, snap: Snapshot): Promise<Respuesta | null> {
  const recuperado = await contextoDe(q, snap);
  if (!recuperado) return null;
  const { contexto, motor, degradado } = recuperado;

  const client = new OpenAI({ apiKey: AI_API_KEY, baseURL: AI_BASE_URL });
  const tool = {
    type: "function" as const,
    function: {
      name: "responder",
      description:
        "Responde con base en el contexto recuperado y lista los slugs de los productos citados.",
      parameters: {
        type: "object",
        properties: {
          texto: { type: "string", description: "Respuesta en español, 2 a 5 frases." },
          slugs: { type: "array", items: { type: "string" }, description: "Slugs citados, máximo 8." },
        },
        required: ["texto", "slugs"],
      },
    },
  };
  const sistema = [
    "Eres el analista de Precio Vivo (precios mayoristas del Gran Mercado Mayorista de Lima).",
    "Respondes en español peruano, claro y sobrio, sin emojis.",
    "REGLAS ABSOLUTAS:",
    "1. Usa EXCLUSIVAMENTE los datos del contexto. Nunca inventes cifras.",
    "2. El bloque GARANTIZADO son los hechos del producto preguntado; el RECUPERADO es contexto por relevancia. Prioriza el garantizado.",
    "3. NO expliques CAUSAS que el dato no respalde. Los datos dicen QUÉ pasó (precio, volumen, anomalía, co-movimiento), no POR QUÉ.",
    "   Si preguntan por qué subió algo, describe el movimiento y el contexto medible, y di que la causa no está en los datos.",
    "4. Los pronósticos son estimaciones beta, nunca cifras del reporte.",
    "5. Si el contexto no alcanza, dilo.",
  ].join("\n");

  const resp = await client.chat.completions.create({
    model: AI_MODEL,
    max_tokens: 900,
    tools: [tool],
    tool_choice: { type: "function", function: { name: "responder" } },
    messages: [
      { role: "system", content: sistema },
      { role: "user", content: `CONTEXTO:\n${contexto}\n\nPregunta del usuario: ${q}` },
    ],
  });

  const call = resp.choices[0]?.message?.tool_calls?.[0];
  if (!call || call.type !== "function") return null;
  let input: { texto?: unknown; slugs?: unknown };
  try {
    input = JSON.parse(call.function.arguments) as { texto?: unknown; slugs?: unknown };
  } catch {
    return null;
  }
  const texto = typeof input.texto === "string" ? input.texto : "";
  if (!texto) return null;
  const slugs = Array.isArray(input.slugs)
    ? input.slugs.filter((s): s is string => typeof s === "string")
    : [];

  // El precio SIEMPRE sale del snapshot, nunca del texto del modelo.
  const porSlug = new Map(snap.productos.map((p) => [p.slug, p]));
  const productos: Fila[] = [];
  for (const s of slugs.slice(0, 8)) {
    const p = porSlug.get(s);
    if (p) productos.push(fila(p));
  }
  // Si el respaldo local degradó, la parte VECTORIAL no corrió; BM25 y el piso
  // sí. Decirlo es la diferencia entre una degradación declarada y una mentira.
  // Por la API no hay peldaño intermedio: o devuelve contexto completo, o
  // devuelve null y ya estaríamos en el respaldo.
  return degradado
    ? { texto, productos, fuente: "llm-rag-lexico", motor, degradado }
    : { texto, productos, fuente: "llm-rag", motor };
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

  if (!AI_API_KEY) {
    // POR QUÉ ESTO SE REGISTRA Y NO SE CALLA
    // La escalera de abajo degrada en silencio a propósito: el usuario recibe
    // una respuesta con datos reales pase lo que pase. El efecto secundario es
    // que desde fuera "no hay clave configurada" y "el proveedor falló" se ven
    // EXACTAMENTE igual: un 200 con fuente "fallback".
    //
    // Costó una tarde averiguar cuál de las dos era. Un fallo que degrada bien
    // pero no deja rastro no es observable, y algo que no se puede observar no
    // se puede arreglar.
    console.error(
      "[consulta] sin AI_API_KEY ni DEEPSEEK_API_KEY en el entorno: " +
        "se responde por palabras clave, sin modelo ni recuperación. " +
        "Configúralas en Vercel > Settings > Environment Variables (Production) " +
        "y vuelve a desplegar.",
    );
    return NextResponse.json(fallback(q, snap) satisfies Respuesta);
  }

  // Escalera de degradación, de más a menos capaz. Cada peldaño responde con
  // datos reales; lo que cambia es cuánta evidencia ve el modelo.
  //   1. RAG        — contexto recuperado (serie, anomalías, ficha)
  //   2. catálogo   — una línea por producto, solo el día de hoy
  //   3. fallback   — palabras clave, sin modelo
  try {
    const conRag = await conRAG(q, snap);
    if (conRag) return NextResponse.json(conRag satisfies Respuesta);
    console.error(
      "[consulta] el RAG no devolvió contexto (índice ausente, firma del " +
        "embebedor distinta a la del artefacto, o EMBED_API_KEY sin configurar). " +
        "Se baja al peldaño de catálogo.",
    );
  } catch (e) {
    console.error("[consulta] el peldaño RAG falló:", nombreDeError(e));
  }
  try {
    return NextResponse.json((await conLLM(q, snap)) satisfies Respuesta);
  } catch (e) {
    console.error("[consulta] el peldaño catálogo falló:", nombreDeError(e));
    return NextResponse.json(fallback(q, snap) satisfies Respuesta);
  }
}

/** Nombre y mensaje del error, nunca el objeto entero.
 *
 *  El cliente de OpenAI adjunta la petición —cabeceras incluidas— al error, así
 *  que volcarlo tal cual escribiría la clave de API en los logs de Vercel. */
function nombreDeError(e: unknown): string {
  if (e instanceof Error) return `${e.name}: ${e.message.slice(0, 200)}`;
  return String(e).slice(0, 200);
}
