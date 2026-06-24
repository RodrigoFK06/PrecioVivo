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
  fuente: "llm" | "fallback";
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

// ---------------------------------------------------------------------------
// Estacionalidad: comparar el precio actual contra el mismo mes en años previos.
// Todos los helpers de abajo son PUROS y seguros en servidor y cliente (sin fs).
// ---------------------------------------------------------------------------

/** Extrae el mes (1..12) de una fecha ISO "YYYY-MM-DD" sin construir Date (evita TZ). */
function mesDe(iso: string): number {
  return +iso.slice(5, 7);
}

/**
 * Promedio de `precio_kg` del producto para un mes calendario dado (1..12),
 * agregando TODOS los años presentes en su serie.
 * Ej.: promedioMensualHistorico(papa, 6) = promedio de todos los junios.
 * @param producto Producto con su serie histórica.
 * @param mes Mes calendario 1..12 (enero=1).
 * @returns Promedio en S//kg, o `null` si no hay datos válidos ese mes.
 */
export function promedioMensualHistorico(
  producto: Producto,
  mes: number,
): number | null {
  const vals = producto.series
    .filter((p) => p.precio_kg != null && mesDe(p.fecha) === mes)
    .map((p) => p.precio_kg as number);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

/**
 * Estacionalidad del producto para su MES VIGENTE (el mes de su último dato).
 * Compara el precio actual (latest.precio_kg) contra el promedio histórico de
 * ese mismo mes en todos los años de la serie.
 * @param producto Producto con serie + latest.
 * @returns `{ mes, promedio, actual, desviacionPct }` o `null` si falta data.
 *   - `mes`: 1..12, el mes del último dato.
 *   - `promedio`: promedio histórico del producto para ese mes (S//kg).
 *   - `actual`: precio actual (latest.precio_kg, S//kg).
 *   - `desviacionPct`: % del actual sobre el promedio histórico
 *     (>0 = más caro que su mes típico; <0 = más barato).
 */
export function estacionalDelProducto(producto: Producto): {
  mes: number;
  promedio: number;
  actual: number;
  desviacionPct: number;
} | null {
  const actual = producto.latest.precio_kg;
  const fechaRef = producto.latest.fecha ?? producto.series.at(-1)?.fecha;
  if (actual == null || !fechaRef) return null;
  const mes = mesDe(fechaRef);
  const promedio = promedioMensualHistorico(producto, mes);
  if (promedio == null || promedio === 0) return null;
  const desviacionPct = ((actual - promedio) / promedio) * 100;
  return { mes, promedio, actual, desviacionPct };
}

// ---------------------------------------------------------------------------
// Exportación CSV (pura, server/cliente-safe). Sin BOM; separador coma.
// ---------------------------------------------------------------------------

/** Escapa un campo CSV: comillas si contiene coma, comilla o salto de línea. */
function csvCampo(v: string | number | null | undefined): string {
  if (v == null) return "";
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Serializa productos a CSV (cabecera + filas). Cadena lista para descargar.
 * @param productos Lista de productos.
 * @param modo
 *   - `'latest'`: una fila por producto. Columnas:
 *     `nombre,precio_kg,var_pct,tendencia,ingreso_hoy,forecast`
 *     (ingreso_hoy = latest.masa_hoy; forecast = forecast.precio_estimado o vacío).
 *   - `'series'`: una fila por punto de serie. Columnas:
 *     `fecha,producto,precio_kg` (omite puntos con precio nulo).
 * @returns CSV con saltos de línea "\n".
 */
export function productosToCSV(
  productos: Producto[],
  modo: "latest" | "series",
): string {
  if (modo === "series") {
    const filas: string[] = ["fecha,producto,precio_kg"];
    for (const p of productos) {
      for (const pt of p.series) {
        if (pt.precio_kg == null) continue;
        filas.push(
          [csvCampo(pt.fecha), csvCampo(p.nombre), csvCampo(pt.precio_kg)].join(","),
        );
      }
    }
    return filas.join("\n");
  }
  // modo 'latest'
  const filas: string[] = [
    "nombre,precio_kg,var_pct,tendencia,ingreso_hoy,forecast",
  ];
  for (const p of productos) {
    filas.push(
      [
        csvCampo(p.nombre),
        csvCampo(p.latest.precio_kg),
        csvCampo(p.latest.var_pct),
        csvCampo(p.latest.tendencia),
        csvCampo(p.latest.masa_hoy),
        csvCampo(p.forecast?.precio_estimado ?? null),
      ].join(","),
    );
  }
  return filas.join("\n");
}

// ---------------------------------------------------------------------------
// Serie alineada para el comparador multi-producto.
// ---------------------------------------------------------------------------

/** Fila alineada: una fecha + el precio de cada producto (slug => precio|null). */
export type SerieAlineadaFila = {
  fecha: string;
  precios: Record<string, number | null>;
};

/**
 * Alinea las series de varios productos sobre un eje de fechas común (unión de
 * todas las fechas presentes, orden ascendente). Pensado para graficar varios
 * productos juntos en el comparador.
 * @param productos Productos a alinear (se indexan por `slug`).
 * @param dias Si se indica, limita a los últimos N días (las N fechas finales).
 * @returns Objeto con:
 *   - `slugs`: slugs en el orden recibido.
 *   - `nombres`: mapa slug => nombre (para leyendas).
 *   - `filas`: array de `{ fecha, precios }`; `precios[slug]` es number o null
 *     si ese producto no tiene dato en esa fecha.
 */
export function serieAlineada(
  productos: Producto[],
  dias?: number,
): {
  slugs: string[];
  nombres: Record<string, string>;
  filas: SerieAlineadaFila[];
} {
  const slugs = productos.map((p) => p.slug);
  const nombres: Record<string, string> = {};
  // mapa: fecha -> (slug -> precio)
  const porFecha = new Map<string, Record<string, number | null>>();
  for (const p of productos) {
    nombres[p.slug] = p.nombre;
    for (const pt of p.series) {
      let fila = porFecha.get(pt.fecha);
      if (!fila) {
        fila = {};
        porFecha.set(pt.fecha, fila);
      }
      fila[p.slug] = pt.precio_kg;
    }
  }
  let fechas = [...porFecha.keys()].sort();
  if (dias != null && dias > 0 && fechas.length > dias) {
    fechas = fechas.slice(-dias);
  }
  const filas: SerieAlineadaFila[] = fechas.map((fecha) => {
    const base = porFecha.get(fecha) ?? {};
    const precios: Record<string, number | null> = {};
    for (const slug of slugs) precios[slug] = base[slug] ?? null;
    return { fecha, precios };
  });
  return { slugs, nombres, filas };
}
