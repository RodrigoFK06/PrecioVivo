export type Point = {
  fecha: string;
  precio_kg: number | null;
  masa_hoy: number | null;
  masa_7d: number | null;
  tendencia: string | null;
};

export type Latest = {
  fecha: string;
  precio_kg: number | null;
  precio_ayer_kg: number | null;
  var_pct: number | null;
  masa_hoy: number | null;
  masa_7d: number | null;
  tendencia: string | null;
};

/**
 * Pronóstico BETA. Se basa en baselines de PRECIO (no de volumen).
 * El kill-gate (ver KillGate) mostró que el volumen NO mejora la predicción,
 * así que el volumen es señal de oferta/anomalía, no predictor.
 */
export type Forecast = {
  metodo: string;
  horizonte_dias: number;
  precio_estimado: number;
  /** [lo, hi] intervalo del estimado. */
  intervalo: [number, number];
  mae_modelo: number;
  mae_baseline: number;
  n_obs: number;
};

/**
 * Resultado del experimento metodológico (honesto): ¿el volumen ayuda a
 * predecir el precio? volume_helps=false significa que NO.
 */
export type KillGate = {
  volume_helps: boolean;
  mae_baseline: number;
  mae_con_volumen: number;
  mejora_pct: number;
  detalle: string;
  n_productos: number;
};

export type Anomalia = {
  slug: string;
  nombre: string;
  tipo: "precio" | "volumen";
  fecha: string;
  z: number;
  detalle: string;
};

export type ResumenIA = {
  texto: string;
  fuente: "claude" | "fallback";
};

export type Producto = {
  nombre: string;
  slug: string;
  categoria: string | null;
  unidad: string;
  equiv_kg: number | null;
  series: Point[];
  latest: Latest;
  forecast?: Forecast;
};

export type Snapshot = {
  generatedAt: string;
  mercado: string;
  latestFecha: string;
  fechas: string[];
  productCount: number;
  attribution: string;
  productos: Producto[];
  forecastMeta?: { kill_gate: KillGate };
  anomalias?: Anomalia[];
  resumenIA?: ResumenIA;
};

// Local-dev data bridge: read the JSON the Python pipeline exports.
// In production this module is the single swap point to Supabase.
export async function getSnapshot(): Promise<Snapshot> {
  // Imports diferidos para que este módulo sea seguro en el cliente:
  // los componentes "use client" (p. ej. ProductTable) importan tipos y
  // utilidades puras de aquí sin arrastrar 'fs'/'path' al bundle del navegador.
  const { promises: fs } = await import("node:fs");
  const path = await import("node:path");
  const p = path.join(process.cwd(), "data", "snapshot.json");
  const raw = await fs.readFile(p, "utf-8");
  return JSON.parse(raw) as Snapshot;
}

export async function getProducto(slug: string): Promise<Producto | null> {
  const s = await getSnapshot();
  return s.productos.find((p) => p.slug === slug) ?? null;
}

/** Products with the largest price move vs the previous day. */
export function movers(s: Snapshot, dir: "up" | "down", n = 6): Producto[] {
  const withVar = s.productos.filter(
    (p) => p.latest.var_pct != null && p.latest.precio_kg != null,
  );
  withVar.sort((a, b) => b.latest.var_pct! - a.latest.var_pct!);
  return dir === "up" ? withVar.slice(0, n) : withVar.slice(-n).reverse();
}

/** Products whose volume (masa_hoy) most exceeds their 7-day average — supply signal. */
export function supplySurges(s: Snapshot, n = 4): Producto[] {
  const withVol = s.productos.filter(
    (p) => p.latest.masa_hoy != null && p.latest.masa_7d != null && p.latest.masa_7d! > 0,
  );
  withVol.sort(
    (a, b) =>
      b.latest.masa_hoy! / b.latest.masa_7d! - a.latest.masa_hoy! / a.latest.masa_7d!,
  );
  return withVol.slice(0, n);
}

/** Anomalías ordenadas por magnitud absoluta del z-score (server-safe). */
export function topAnomalias(s: Snapshot, n = 6): Anomalia[] {
  const arr = s.anomalias ?? [];
  return [...arr].sort((a, b) => Math.abs(b.z) - Math.abs(a.z)).slice(0, n);
}

/** Pronóstico de un producto, si existe (server-safe, sin null). */
export function forecastDe(p: Producto): Forecast | null {
  return p.forecast ?? null;
}

/**
 * Filtro puro y server/cliente-safe: busca productos por nombre,
 * insensible a acentos y mayúsculas. Vacío => devuelve todo.
 */
export function buscarProductos(productos: Producto[], q: string): Producto[] {
  const norm = (s: string) =>
    s
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .trim();
  const needle = norm(q);
  if (!needle) return productos;
  return productos.filter((p) => norm(p.nombre).includes(needle));
}

export function stats(series: Point[]) {
  const vals = series.map((p) => p.precio_kg).filter((v): v is number => v != null);
  if (!vals.length) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  return { min, max, avg, first: vals[0], last: vals[vals.length - 1] };
}
