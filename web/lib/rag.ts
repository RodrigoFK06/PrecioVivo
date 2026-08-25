/**
 * Recuperación (RAG) del sitio: lee el índice estático que publica el pipeline.
 *
 * POR QUÉ EL ÍNDICE VIAJA EN EL DEPLOY
 * ------------------------------------
 * El sitio es estático + snapshot.json. Meter una base de datos en el camino de
 * cada pregunta sería cambiar su arquitectura por una capacidad que cabe en ~3 MB.
 * El costo honesto: en producción pgvector no participa — vive en el pipeline y
 * en el docker-compose. Ver README, sección RAG.
 *
 * QUÉ CORRE AQUÍ Y QUÉ NO
 * -----------------------
 * El pipeline (CLI y evaluaciones) hace búsqueda HÍBRIDA: BM25 + vectores
 * fusionados con RRF. Aquí corre el filtro estructurado, la búsqueda vectorial y
 * el piso determinista — pero NO BM25.
 *
 * No es un olvido: portar BM25 y el parser de fechas en español a TypeScript
 * serían dos implementaciones de las mismas reglas, que divergen en cuanto una
 * de las dos se toque. El bloque 7 expone el pipeline como API con FastAPI, y
 * ahí el sitio pasa a llamar a la MISMA implementación en vez de a una copia.
 * Hasta entonces, esto es un subconjunto declarado, no una equivalencia.
 *
 * El piso determinista sí está completo, que es lo que garantiza que una
 * pregunta por un producto nunca se quede sin sus hechos.
 */
import { promises as fs } from "fs";
import path from "path";
import { gunzipSync } from "zlib";

const EMBED_BASE_URL = process.env.EMBED_BASE_URL ?? "https://api.jina.ai/v1";
// Espejo de `embeddings.EMBED_MODEL`. El default describe el índice que este
// sitio consulta de verdad; ver el comentario largo allí. Si aquí quedara un
// modelo que no es el del artefacto, perder la variable de entorno apagaría la
// recuperación vectorial SIN error visible.
const EMBED_MODEL = process.env.EMBED_MODEL ?? "jina-embeddings-v3";
const EMBED_DIMS = Number(process.env.EMBED_DIMS ?? "256");
const EMBED_API_KEY = process.env.EMBED_API_KEY ?? process.env.OPENAI_API_KEY;

/** Debe coincidir con indexer.ESCALA_INT8. */
const ESCALA_INT8 = 127;

/** Tope de chunks del piso. Espeja retrieval.PISO_PRESUPUESTO. */
const PISO_PRESUPUESTO = 14;
const PISO_MERCADO_DIA = 3;

export type RagChunk = {
  id: string;
  tipo: "producto-periodo" | "producto-perfil" | "evento-anomalia" | "mercado-dia";
  texto: string;
  slug: string | null;
  /** fecha_inicio */
  d0: string;
  /** fecha_fin */
  d1: string;
};

type ParteMeta = {
  firma_embedder: string;
  dims: number;
  granularidad: string;
  n_chunks: number;
  huella_corpus: string;
  parte: string;
  corte: string;
};

export type Indice = {
  chunks: RagChunk[];
  /** Vectores concatenados, fila i = chunks[i]. Ya des-cuantizados. */
  vectores: Float32Array;
  dims: number;
  firmaEmbedder: string;
  granularidad: string;
};

export type Filtro = {
  slugs?: Set<string>;
  desde?: string;
  hasta?: string;
};

export type ResultadoRag = { chunk: RagChunk; score: number };

export type ContextoRag = {
  piso: RagChunk[];
  recuperados: ResultadoRag[];
  slugs: Set<string>;
  desde?: string;
  hasta?: string;
  /** Por qué NO se pudo usar el RAG, si es el caso. */
  degradado?: string;
};

// ---------------------------------------------------------------------------
// Carga del artefacto (memoizada por proceso)
// ---------------------------------------------------------------------------
let cache: Promise<Indice | null> | null = null;

async function leerParte(
  dir: string,
  nombre: string,
): Promise<{ meta: ParteMeta; chunks: RagChunk[]; crudo: Int8Array } | null> {
  try {
    const [gz, bin] = await Promise.all([
      fs.readFile(path.join(dir, `${nombre}.json.gz`)),
      fs.readFile(path.join(dir, `${nombre}.bin`)),
    ]);
    const payload = JSON.parse(gunzipSync(gz).toString("utf-8")) as {
      meta: ParteMeta;
      chunks: RagChunk[];
    };
    return {
      meta: payload.meta,
      chunks: payload.chunks,
      crudo: new Int8Array(bin.buffer, bin.byteOffset, bin.byteLength),
    };
  } catch {
    // Parte ausente: el índice puede existir solo con la cola reciente.
    return null;
  }
}

export async function cargarIndice(): Promise<Indice | null> {
  if (cache) return cache;
  cache = (async () => {
    const dir = path.join(process.cwd(), "data");
    const partes = (
      await Promise.all([leerParte(dir, "rag-historico"), leerParte(dir, "rag-reciente")])
    ).filter((p): p is NonNullable<typeof p> => p !== null);
    if (!partes.length) return null;

    const dims = partes[0].meta.dims;
    const firmas = new Set(partes.map((p) => p.meta.firma_embedder));
    if (firmas.size > 1) {
      // Partes construidas con embebedores distintos: sus vectores no viven en
      // el mismo espacio y mezclarlos daría ruido con total confianza.
      throw new Error(
        `Índice RAG inconsistente: partes con embebedores distintos (${[...firmas].join(", ")}). ` +
          `Reconstruye completo con: python -m preciovivo.ingest --index`,
      );
    }

    const total = partes.reduce((n, p) => n + p.chunks.length, 0);
    const vectores = new Float32Array(total * dims);
    const chunks: RagChunk[] = [];
    let off = 0;
    for (const p of partes) {
      for (let i = 0; i < p.crudo.length; i++) vectores[off + i] = p.crudo[i] / ESCALA_INT8;
      off += p.crudo.length;
      chunks.push(...p.chunks);
    }
    return {
      chunks,
      vectores,
      dims,
      firmaEmbedder: partes[0].meta.firma_embedder,
      granularidad: partes[0].meta.granularidad,
    };
  })();
  return cache;
}

// ---------------------------------------------------------------------------
// Embedding de la consulta
// ---------------------------------------------------------------------------
/**
 * Embebe la pregunta con el MISMO modelo que construyó el índice.
 *
 * Es la razón por la que el índice publicado se construye con el embebedor de
 * API y no con el local: Vercel no puede correr un modelo, así que la consulta
 * se embebe por HTTP, y consulta y corpus tienen que vivir en el mismo espacio
 * vectorial o el coseno compara cosas incomparables.
 */
/**
 * Espera máxima ante un 429 antes de rendirse, en milisegundos.
 *
 * Al otro lado hay una persona esperando una respuesta HTTP, así que la
 * disciplina es la contraria a la del backfill: allí esperar 34 s es correcto,
 * aquí sería una petición colgada. Se reintenta UNA vez si el proveedor pide una
 * espera corta; si pide más, se degrada — y la degradación ahora se reporta,
 * que es lo que la hace aceptable.
 */
const MAX_ESPERA_429_MS = 1500;

function esperar(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Pide el embedding, con un reintento acotado ante 429.
 *
 * Hace falta porque los free tier tienen cuotas por minuto muy bajas (Gemini:
 * 100 embeddings/min). Sin esto, una ráfaga de tráfico convierte cada consulta
 * en una degradación silenciosa: `recuperar` captura el error, devuelve el piso
 * y la respuesta sale igual.
 */
async function pedirEmbedding(q: string): Promise<Response> {
  const enviar = () =>
    fetch(`${EMBED_BASE_URL}/embeddings`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${EMBED_API_KEY}`,
      },
      body: JSON.stringify({ model: EMBED_MODEL, input: q, dimensions: EMBED_DIMS }),
    });

  const primera = await enviar();
  if (primera.status !== 429) return primera;

  const cabecera = primera.headers?.get?.("retry-after");
  const esperaMs = cabecera ? Number(cabecera) * 1000 : MAX_ESPERA_429_MS;
  if (!Number.isFinite(esperaMs) || esperaMs > MAX_ESPERA_429_MS) return primera;

  await esperar(esperaMs);
  return enviar();
}

export async function embedConsulta(q: string, firmaEsperada: string): Promise<Float32Array> {
  if (!EMBED_API_KEY) {
    throw new Error("Falta EMBED_API_KEY para embeber la consulta (AI_API_KEY es de DeepSeek, que no ofrece embeddings).");
  }
  const firmaActual = `api:${EMBED_MODEL}:${EMBED_DIMS}`;
  if (firmaActual !== firmaEsperada) {
    throw new Error(
      `El índice se construyó con '${firmaEsperada}' y este entorno embebe con '${firmaActual}'. ` +
        `Ajusta EMBED_MODEL/EMBED_DIMS o reconstruye el índice.`,
    );
  }

  const resp = await pedirEmbedding(q);
  if (!resp.ok) {
    throw new Error(`El proveedor de embeddings respondió ${resp.status}`);
  }
  const data = (await resp.json()) as { data: { embedding: number[] }[] };
  const v = data.data?.[0]?.embedding;
  if (!v) throw new Error("Respuesta de embeddings sin vector");

  // L2-normalizar: el pipeline garantiza norma 1 en el corpus, así que el
  // coseno es un producto punto. La consulta tiene que cumplir lo mismo.
  const out = new Float32Array(v.length);
  let n = 0;
  for (const x of v) n += x * x;
  n = Math.sqrt(n) || 1;
  for (let i = 0; i < v.length; i++) out[i] = v[i] / n;
  return out;
}

// ---------------------------------------------------------------------------
// Filtro + búsqueda
// ---------------------------------------------------------------------------
/** Solapamiento de rangos, igual que vectorstore.Filtro.acepta. */
export function acepta(c: RagChunk, f: Filtro): boolean {
  if (f.slugs && (c.slug === null || !f.slugs.has(c.slug))) return false;
  if (f.desde && c.d1 && c.d1 < f.desde) return false;
  if (f.hasta && c.d0 && c.d0 > f.hasta) return false;
  return true;
}

export function buscar(idx: Indice, q: Float32Array, k: number, f?: Filtro): ResultadoRag[] {
  const { chunks, vectores, dims } = idx;
  const res: ResultadoRag[] = [];
  for (let i = 0; i < chunks.length; i++) {
    if (f && !acepta(chunks[i], f)) continue;
    let s = 0;
    const base = i * dims;
    for (let j = 0; j < dims; j++) s += vectores[base + j] * q[j];
    res.push({ chunk: chunks[i], score: s });
  }
  // Orden total: score y, ante empate, id. Espeja
  // vectorstore._ordenar_determinista para que el sitio no reordene distinto.
  res.sort((a, b) => b.score - a.score || (a.chunk.id < b.chunk.id ? -1 : 1));
  return res.slice(0, k);
}

// ---------------------------------------------------------------------------
// BM25 (léxico)
//
// POR QUÉ AHORA SÍ ESTÁ EN EL SITIO
// ---------------------------------
// Estaba deliberadamente fuera: portar BM25 a TypeScript es una segunda
// implementación de las mismas reglas, y dos implementaciones divergen. El plan
// era que el sitio llamara a la API del pipeline en vez de copiarla.
//
// Lo que cambió el cálculo: el embebido de la CONSULTA depende de un proveedor
// externo con cuota por minuto. Cuando esa cuota se agota —o el proveedor
// falla— el sitio se quedaba solo con el piso determinista, sin ningún
// recuperador. BM25 no depende de nadie: corre sobre el texto que ya viaja en
// el deploy. Es la única pieza de recuperación que no se puede caer.
//
// La deriva se controla igual que el resto del port: `web/test/rag.test.ts`
// espeja los casos de `pipeline/tests/test_retrieval.py`, con las mismas
// constantes (k1, b, C de RRF) declaradas al lado de su equivalente en Python.
// ---------------------------------------------------------------------------

/** Espejan retrieval.BM25 (k1, b) y retrieval.RRF_C / CANDIDATOS. */
const BM25_K1 = 1.5;
const BM25_B = 0.75;
const RRF_C = 60;
const CANDIDATOS = 40;

export type IndiceBM25 = {
  /** token -> [(índice del chunk, frecuencia en ese chunk)] */
  postings: Map<string, Array<[number, number]>>;
  largos: number[];
  largoMedio: number;
  idf: Map<string, number>;
};

export function construirBM25(chunks: RagChunk[]): IndiceBM25 {
  const postings = new Map<string, Array<[number, number]>>();
  const largos: number[] = [];

  for (let i = 0; i < chunks.length; i++) {
    const tokens = tokenizar(chunks[i].texto);
    largos.push(tokens.length);
    const frec = new Map<string, number>();
    for (const t of tokens) frec.set(t, (frec.get(t) ?? 0) + 1);
    for (const [t, f] of frec) {
      const lista = postings.get(t);
      if (lista) lista.push([i, f]);
      else postings.set(t, [[i, f]]);
    }
  }

  const n = chunks.length;
  const largoMedio = n ? largos.reduce((a, b) => a + b, 0) / n : 0;
  const idf = new Map<string, number>();
  for (const [t, post] of postings) {
    idf.set(t, Math.log(1 + (n - post.length + 0.5) / (post.length + 0.5)));
  }
  return { postings, largos, largoMedio, idf };
}

/** Devuelve [(índice del chunk, score)] ordenado, top-k. Espeja BM25.buscar. */
export function buscarBM25(
  bm: IndiceBM25,
  chunks: RagChunk[],
  pregunta: string,
  k: number,
  permitidos?: Set<number>,
): Array<[number, number]> {
  const scores = new Map<number, number>();
  for (const tok of new Set(tokenizar(pregunta))) {
    const post = bm.postings.get(tok);
    if (!post) continue;
    const idf = bm.idf.get(tok)!;
    for (const [i, f] of post) {
      if (permitidos && !permitidos.has(i)) continue;
      const norm =
        1 - BM25_B + BM25_B * (bm.largoMedio ? bm.largos[i] / bm.largoMedio : 1);
      const aporte = (idf * (f * (BM25_K1 + 1))) / (f + BM25_K1 * norm);
      scores.set(i, (scores.get(i) ?? 0) + aporte);
    }
  }
  // Desempate por id de chunk, igual que el vectorial: orden reproducible.
  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1] || (chunks[a[0]].id < chunks[b[0]].id ? -1 : 1))
    .slice(0, k);
}

/**
 * Reciprocal Rank Fusion. Espeja retrieval.rrf.
 *
 * Combina rankings por POSICIÓN, así que no hay que calibrar dos escalas
 * incomparables: BM25 no está acotado y el coseno vive en [-1, 1].
 */
export function rrf(listas: string[][], c = RRF_C): Map<string, number> {
  const out = new Map<string, number>();
  for (const lista of listas) {
    lista.forEach((id, i) => out.set(id, (out.get(id) ?? 0) + 1 / (c + i + 1)));
  }
  return out;
}

// El índice léxico se construye una vez por proceso, la primera vez que se
// consulta. No va dentro de `cargarIndice` para no pagarlo en arranques que
// nunca lleguen a preguntar nada.
let cacheBM25: { para: Indice; bm: IndiceBM25 } | null = null;

export function bm25De(idx: Indice): IndiceBM25 {
  if (cacheBM25 && cacheBM25.para === idx) return cacheBM25.bm;
  const bm = construirBM25(idx.chunks);
  cacheBM25 = { para: idx, bm };
  return bm;
}

// ---------------------------------------------------------------------------
// Piso determinista
// ---------------------------------------------------------------------------
/**
 * Los hechos que se garantizan pase lo que pase con la búsqueda.
 *
 * Espeja retrieval.Recuperador._piso. Un asistente que a veces falla lo obvio
 * ("¿a cuánto está la papa?") destruye la confianza en todas sus respuestas,
 * incluidas las buenas — así que esto no depende de un coseno.
 */
export function piso(idx: Indice, slugs: Set<string>, desde?: string, hasta?: string): RagChunk[] {
  if (slugs.size === 0) {
    const dias = idx.chunks.filter(
      (c) => c.tipo === "mercado-dia" && acepta(c, { desde, hasta }),
    );
    dias.sort((a, b) => (a.d1 < b.d1 ? 1 : -1));
    return dias.slice(0, PISO_MERCADO_DIA);
  }

  const cupo = Math.max(1, Math.floor(PISO_PRESUPUESTO / slugs.size));
  const acotaFechas = Boolean(desde || hasta);
  const out: RagChunk[] = [];

  for (const slug of [...slugs].sort()) {
    const delProd = idx.chunks.filter((c) => c.slug === slug);
    const perfil = delProd.filter((c) => c.tipo === "producto-perfil");
    const periodos = delProd
      .filter((c) => c.tipo === "producto-periodo")
      .sort((a, b) => (a.d1 < b.d1 ? 1 : -1));
    const anomalias = delProd
      .filter((c) => c.tipo === "evento-anomalia")
      .sort((a, b) => (a.d1 < b.d1 ? 1 : -1))
      .slice(0, 3);

    let preferencia: RagChunk[];
    if (acotaFechas) {
      const enRango = periodos.filter((c) => acepta(c, { desde, hasta }));
      const fuera = periodos.filter((c) => !acepta(c, { desde, hasta }));
      preferencia = [...enRango, ...perfil, ...fuera.slice(0, 4), ...anomalias];
    } else {
      preferencia = [...perfil, ...periodos.slice(0, 4), ...anomalias];
    }
    out.push(...preferencia.slice(0, cupo));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Parseo de la pregunta
//
// Port de retrieval.detectar_productos / detectar_rango_fechas. Es el punto
// exacto donde las dos implementaciones pueden divergir, así que los tests de
// `web/test/rag.test.ts` espejan los casos de `pipeline/tests/test_retrieval.py`:
// si una de las dos deriva, sus tests fallan. La solución de fondo es el bloque
// 7 (FastAPI), donde el sitio llama a la implementación de Python en vez de a
// una copia.
// ---------------------------------------------------------------------------
const PALABRAS_VACIAS = new Set([
  "que", "cual", "cuales", "como", "cuando", "donde", "por", "para", "con",
  "sin", "del", "las", "los", "una", "uno", "esta", "este", "esa", "ese",
  "mas", "menos", "muy", "hoy", "ayer", "semana", "mes", "ano", "dia", "dias",
  "precio", "precios", "costo", "cuesta", "vale", "subio", "bajo", "sube",
  "baja", "mercado", "kilo", "kilos", "soles", "toneladas", "ingreso",
]);

const MESES: Record<string, number> = {
  enero: 1, febrero: 2, marzo: 3, abril: 4, mayo: 5, junio: 6, julio: 7,
  agosto: 8, setiembre: 9, septiembre: 9, octubre: 10, noviembre: 11, diciembre: 12,
};

export function normalizar(s: string): string {
  return (s ?? "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
}

export function tokenizar(s: string): string[] {
  return normalizar(s).split(/[^a-z0-9]+/).filter(Boolean);
}

function tokensSignificativos(nombre: string): string[] {
  return tokenizar(nombre).filter((t) => t.length > 2 && !PALABRAS_VACIAS.has(t));
}

/**
 * Ver retrieval.detectar_productos: específico (>=2 tokens) o agrupado (1).
 *
 * La supresión del nivel agrupado es POR FAMILIA, no global. Era global, y eso
 * hacía que bastara con que UN producto llegara a dos tokens para descartar
 * todos los de uno: "compara el tomate con la papa blanca" perdía el tomate y
 * el piso llenaba sus ocho plazas con papa. Cualquier comparación entre nombres
 * de distinta longitud respondía con la mitad de la evidencia.
 *
 * Esta copia es la que sirve a los usuarios: arreglarlo solo en Python no habría
 * llegado a producción. El comentario de arriba avisa de justo esto.
 */
export function detectarProductos(pregunta: string, catalogo: Record<string, string>): Set<string> {
  const enPregunta = new Set(tokenizar(pregunta));
  if (!enPregunta.size) return new Set();

  const aciertos = new Map<string, number>();
  const cabezas = new Map<string, string[]>();
  for (const [slug, nombre] of Object.entries(catalogo)) {
    const tokens = tokensSignificativos(nombre);
    if (!tokens.length) continue;
    aciertos.set(slug, tokens.filter((t) => enPregunta.has(t)).length);
    const lista = cabezas.get(tokens[0]) ?? [];
    lista.push(slug);
    cabezas.set(tokens[0], lista);
  }

  const especificos = new Set([...aciertos].filter(([, n]) => n >= 2).map(([s]) => s));

  // Familias ya resueltas por un match específico: dentro de ellas no se
  // expande — quien dijo "papa blanca" no pidió las diez papas.
  const familiasResueltas = new Set(
    [...cabezas].filter(([, slugs]) => slugs.some((s) => especificos.has(s))).map(([c]) => c),
  );

  const agrupados = new Set<string>();
  for (const [cabeza, slugs] of cabezas) {
    if (enPregunta.has(cabeza) && !familiasResueltas.has(cabeza)) {
      slugs.forEach((s) => agrupados.add(s));
    }
  }

  if (especificos.size || agrupados.size) {
    return new Set([...especificos, ...agrupados]);
  }

  // Último recurso, y sigue siendo último a propósito: aquí sobreviven tokens de
  // unidad ("bolsa", "cajon", "docena") que viven en nombres de producto Y en
  // preguntas corrientes. Unirlo a los niveles anteriores convertiría "¿la papa
  // blanca viene en bolsa?" en una consulta sobre el limón.
  return new Set([...aciertos].filter(([, n]) => n === 1).map(([s]) => s));
}

const iso = (d: Date) => d.toISOString().slice(0, 10);
/** Fecha UTC pura: evita que la zona horaria del runtime corra un día. */
const udate = (y: number, m: number, d: number) => new Date(Date.UTC(y, m - 1, d));

export function esEstacional(pregunta: string): boolean {
  return /suele|solia|normalmente|habitual|historic|estacional|tipic|en promedio|todos los anos|cada ano/.test(
    normalizar(pregunta),
  );
}

/** Ver retrieval.detectar_rango_fechas. `ref` es el último dato, no hoy. */
export function detectarRangoFechas(
  pregunta: string,
  ref: string | null,
): { desde?: string; hasta?: string } {
  if (!ref) return {};
  const r = new Date(`${ref}T00:00:00Z`);
  if (Number.isNaN(r.getTime())) return {};
  const t = normalizar(pregunta);
  const [ry, rm, rd] = [r.getUTCFullYear(), r.getUTCMonth() + 1, r.getUTCDate()];
  const meses = Object.keys(MESES).join("|");

  const isoM = t.match(/\b(\d{4}-\d{2}-\d{2})\b/);
  if (isoM) return { desde: isoM[1], hasta: isoM[1] };

  if (esEstacional(pregunta)) return {};

  // "el 15 de julio [de 2026]" -> un día concreto. Antes que el patrón de mes.
  const diaM = t.match(new RegExp(`\\b(\\d{1,2})\\s+de\\s+(${meses})\\b(?:\\s+del?\\s+(\\d{4}))?`));
  if (diaM) {
    const dia = Number(diaM[1]);
    const mes = MESES[diaM[2]];
    let anio = diaM[3] ? Number(diaM[3]) : ry;
    if (!diaM[3] && (mes > rm || (mes === rm && dia > rd))) anio -= 1;
    const d = udate(anio, mes, dia);
    if (d.getUTCMonth() + 1 === mes && d.getUTCDate() === dia) {
      return { desde: iso(d), hasta: iso(d) };
    }
  }

  const mesM = t.match(new RegExp(`\\b(${meses})\\b(?:\\s+de\\s+(\\d{4}))?`));
  if (mesM) {
    const mes = MESES[mesM[1]];
    let anio = mesM[2] ? Number(mesM[2]) : ry;
    if (!mesM[2] && mes > rm) anio -= 1;
    const fin = udate(mes === 12 ? anio + 1 : anio, mes === 12 ? 1 : mes + 1, 1);
    fin.setUTCDate(fin.getUTCDate() - 1);
    return { desde: iso(udate(anio, mes, 1)), hasta: iso(fin) };
  }

  const desplazar = (dias: number) => {
    const d = new Date(r);
    d.setUTCDate(d.getUTCDate() + dias);
    return d;
  };

  if (/\bhoy\b|\bultimo dia\b|\bactual(mente)?\b/.test(t)) return { desde: ref, hasta: ref };
  if (/\bayer\b/.test(t)) return { desde: iso(desplazar(-1)), hasta: iso(desplazar(-1)) };

  const ultN = t.match(/ultim[oa]s?\s+(\d{1,3})\s+dias?/);
  if (ultN) return { desde: iso(desplazar(-Number(ultN[1]))), hasta: ref };

  // getUTCDay(): domingo=0. Se convierte a lunes=0 como Python.
  const lunesOffset = (r.getUTCDay() + 6) % 7;
  if (/semana pasada|semana anterior/.test(t)) {
    return { desde: iso(desplazar(-lunesOffset - 7)), hasta: iso(desplazar(-lunesOffset - 1)) };
  }
  if (/esta semana|ultima semana|semana/.test(t)) {
    return { desde: iso(desplazar(-lunesOffset)), hasta: ref };
  }
  if (/mes pasado|mes anterior/.test(t)) {
    const fin = udate(ry, rm, 1);
    fin.setUTCDate(fin.getUTCDate() - 1);
    return { desde: iso(udate(fin.getUTCFullYear(), fin.getUTCMonth() + 1, 1)), hasta: iso(fin) };
  }
  if (/este mes|ultimo mes/.test(t)) return { desde: iso(udate(ry, rm, 1)), hasta: ref };
  if (/ano pasado|anio pasado/.test(t)) {
    return { desde: `${ry - 1}-01-01`, hasta: `${ry - 1}-12-31` };
  }
  if (/este ano|este anio/.test(t)) return { desde: `${ry}-01-01`, hasta: ref };

  return {};
}

/** Orquesta el parseo, el filtro, la búsqueda híbrida y el piso. */
export async function recuperar(
  pregunta: string,
  catalogo: Record<string, string>,
  fechaRef: string | null,
  k = 8,
): Promise<ContextoRag> {
  const slugs = detectarProductos(pregunta, catalogo);
  const { desde, hasta } = detectarRangoFechas(pregunta, fechaRef);
  const vacio: ContextoRag = { piso: [], recuperados: [], slugs, desde, hasta };

  const idx = await cargarIndice();
  if (!idx) return { ...vacio, degradado: "no hay índice RAG publicado" };

  const base = piso(idx, slugs, desde, hasta);

  // Pre-filtro estructurado, con la misma relajación que retrieval.recuperar:
  // un filtro que no deja nada es peor que no filtrar, porque deja la pregunta
  // sin contexto recuperado.
  let filtro: Filtro | undefined = { slugs: slugs.size ? slugs : undefined, desde, hasta };
  if (!idx.chunks.some((c) => acepta(c, filtro!))) filtro = undefined;

  // 1) LÉXICO. Va primero y siempre: no depende de ningún proveedor externo,
  //    así que es el recuperador que sobrevive a una cuota agotada.
  const permitidos = filtro
    ? new Set(idx.chunks.map((c, i) => (acepta(c, filtro!) ? i : -1)).filter((i) => i >= 0))
    : undefined;
  const lex = buscarBM25(bm25De(idx), idx.chunks, pregunta, CANDIDATOS, permitidos).map(
    ([i]) => idx.chunks[i].id,
  );

  // 2) VECTORIAL. Aporta sinónimos y paráfrasis que el léxico pierde, pero
  //    puede no estar disponible.
  let vec: string[] = [];
  let degradado: string | undefined;
  try {
    const q = await embedConsulta(pregunta, idx.firmaEmbedder);
    vec = buscar(idx, q, CANDIDATOS, filtro).map((r) => r.chunk.id);
  } catch (e) {
    degradado = e instanceof Error ? e.message : String(e);
  }

  // 3) FUSIÓN RRF. Con una sola lista degenera en ese ranking, que es
  //    exactamente lo que se quiere cuando el vectorial no está.
  const fusion = rrf(vec.length ? [lex, vec] : [lex]);
  const porId = new Map(idx.chunks.map((c) => [c.id, c]));
  const recuperados: ResultadoRag[] = [...fusion.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
    .slice(0, k)
    .flatMap(([id, score]) => {
      const chunk = porId.get(id);
      return chunk ? [{ chunk, score }] : [];
    });

  return { ...vacio, piso: base, recuperados, degradado };
}

// ---------------------------------------------------------------------------
// Serialización para el prompt
// ---------------------------------------------------------------------------
export function aPrompt(ctx: ContextoRag): string {
  const partes: string[] = [];
  if (!ctx.slugs.size) {
    // Espeja retrieval.Contexto.a_prompt. Ver ahí el porqué, con las medidas:
    // una pregunta por un producto que NO está en el catálogo recupera fichas
    // de otro a coseno casi idéntico al de una consulta legítima, y ningún
    // umbral los separa. Se resuelve avisando al modelo, no filtrando.
    partes.push(
      "=== AVISO ===\n" +
        "Ninguna palabra de la pregunta coincide con un producto del catálogo. " +
        "Si preguntan por un producto concreto, NO está entre los que seguimos: " +
        "dilo explícitamente y no respondas con el precio de otro producto por " +
        "parecido. El contexto de abajo es lo más cercano que encontró la " +
        "búsqueda, no una coincidencia.",
    );
  }
  if (ctx.piso.length) {
    const que = ctx.slugs.size
      ? "productos que menciona la pregunta"
      : "resumen del mercado en las fechas relevantes";
    partes.push(`=== CONTEXTO GARANTIZADO (${que}) ===`);
    partes.push(...ctx.piso.map((c) => c.texto));
  }
  const yaEstan = new Set(ctx.piso.map((c) => c.id));
  const extra = ctx.recuperados.filter((r) => !yaEstan.has(r.chunk.id));
  if (extra.length) {
    partes.push("=== CONTEXTO RECUPERADO (por relevancia) ===");
    partes.push(...extra.map((r) => r.chunk.texto));
  }
  return partes.join("\n\n");
}
