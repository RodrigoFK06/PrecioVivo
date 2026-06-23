import { promises as fs } from "fs";
import path from "path";

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

export type Producto = {
  nombre: string;
  slug: string;
  categoria: string | null;
  unidad: string;
  equiv_kg: number | null;
  series: Point[];
  latest: Latest;
};

export type Snapshot = {
  generatedAt: string;
  mercado: string;
  latestFecha: string;
  fechas: string[];
  productCount: number;
  attribution: string;
  productos: Producto[];
};

// Local-dev data bridge: read the JSON the Python pipeline exports.
// In production this module is the single swap point to Supabase.
export async function getSnapshot(): Promise<Snapshot> {
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

export function stats(series: Point[]) {
  const vals = series.map((p) => p.precio_kg).filter((v): v is number => v != null);
  if (!vals.length) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  return { min, max, avg, first: vals[0], last: vals[vals.length - 1] };
}
