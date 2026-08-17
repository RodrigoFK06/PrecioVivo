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
 * MAE walk-forward agregado de cada FAMILIA de modelo para un horizonte, sobre
 * el mismo conjunto de productos. Lo calcula `forecast._comparacion_modelos`.
 *
 * Es la única comparación del proyecto que NO tiene sesgo de selección: cada
 * familia se evalúa por separado y ninguna participó en elegir a las otras.
 * Contrasta con `producto.forecast`, donde `metodo` es el argmin sobre un
 * conjunto que ya incluye a `mae_baseline` — ahí "el modelo le gana al
 * baseline" es verdad por construcción y no significa nada.
 */
export type ComparacionHorizonte = {
  mae_baseline: number | null;
  mae_ar1: number | null;
  mae_volumen: number | null;
  mae_gbm: number | null;
  n_productos: number;
  n_productos_gbm: number;
  ganador: "baseline" | "ar1" | "volumen" | "gbm" | null;
  veredicto: string;
};

export type ComparacionModelos = {
  horizontes: number[];
  por_horizonte: Record<string, ComparacionHorizonte>;
  gbm_disponible: boolean;
  umbral_gbm: number;
  veredicto_global: string;
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
  comparacion_modelos?: ComparacionModelos;
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

/**
 * Verificación cruzada (§4): los precios GMML de HOY contrastados contra
 * SISAP–MIDAGRI, una publicación independiente del mismo ministerio.
 * Es una segunda opinión sobre nuestra propia ingesta — no la fuente primaria.
 */
export type VerificacionItem = {
  producto: string;
  sisap_kg: number | null;
  gmml_kg: number | null;
  delta_pct: number | null;
  /** true si |delta| supera el umbral o falta contraparte en nuestra BD. */
  flag: boolean;
  detalle: string;
};

export type Verificacion = {
  fuente: string;
  url: string;
  /** Día GMML contrastado — del que la comparación habla. Decide `vigente`. */
  fecha: string | null;
  /**
   * Fecha del reporte SISAP usado. Puede ir por DELANTE de `fecha`: SISAP
   * publica ~06:30 y también los sábados, cuando el GMML no publica.
   */
  fecha_sisap?: string | null;
  /**
   * Columna de SISAP comparada. 'precio_ayer_kg' cuando el 335 del día de SISAP
   * todavía no existe: esa columna describe el mismo día que nuestro último 335,
   * así que es la comparación correcta y no un apaño.
   */
  columna_sisap?: "precio_hoy_kg" | "precio_ayer_kg";
  /** Descripción legible de qué se comparó contra qué. */
  base?: string;
  /** Umbral de discrepancia en % (|delta| mayor se marca). */
  umbral_pct: number;
  contrastados: number;
  coinciden: number;
  /**
   * false = la BD no tenía NINGÚN precio GMML de esa fecha (el reporte del día
   * aún no se ingestaba): contraste prematuro, no divergencia.
   */
  gmml_disponible: boolean;
  resultados: VerificacionItem[];
  generado_en: string;
  /** true si el contraste corresponde al último día del snapshot. */
  vigente: boolean;
};

/** Mercado adicional al GMML (boletín MMF2, SISAP/aves): solo su último día. */
export type MercadoExtraProducto = {
  nombre: string;
  slug: string;
  precio_kg: number | null;
  var_pct: number | null;
};

export type MercadoExtra = {
  codigo: string;
  nombre: string;
  latestFecha: string;
  productos: MercadoExtraProducto[];
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
  mercados?: MercadoExtra[];
  verificacion?: Verificacion;
};

/**
 * Snapshot memoizado por proceso, invalidado por mtime.
 *
 * `snapshot.json` pesa ~3,4 MB. Sin cache, cada request a una ruta dinámica
 * —`/api/consulta` es `force-dynamic`— lo leía del disco y lo parseaba entero.
 * Llamaba la atención porque el índice RAG, justo al lado, sí estaba memoizado.
 *
 * La clave es el mtime y no un TTL: en producción el archivo solo cambia con un
 * deploy nuevo (proceso nuevo, cache vacío), y en `next dev` un snapshot
 * regenerado se recoge en la siguiente petición sin reiniciar nada. Un `stat`
 * por request es despreciable frente a parsear 3,4 MB.
 */
let cacheSnapshot: { mtimeMs: number; datos: Snapshot } | null = null;

// Local-dev data bridge: read the JSON the Python pipeline exports.
// In production this module is the single swap point to Supabase.
export async function getSnapshot(): Promise<Snapshot> {
  // Imports diferidos para que este módulo sea seguro en el cliente:
  // los componentes "use client" (p. ej. ProductTable) importan tipos y
  // utilidades puras de aquí sin arrastrar 'fs'/'path' al bundle del navegador.
  const { promises: fs } = await import("node:fs");
  const path = await import("node:path");
  const p = path.join(process.cwd(), "data", "snapshot.json");

  const { mtimeMs } = await fs.stat(p);
  if (cacheSnapshot && cacheSnapshot.mtimeMs === mtimeMs) return cacheSnapshot.datos;

  const raw = await fs.readFile(p, "utf-8");
  const datos = JSON.parse(raw) as Snapshot;
  cacheSnapshot = { mtimeMs, datos };
  return datos;
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

// ---------------------------------------------------------------------------
// Comparación honesta de modelos (sin sesgo de selección)
// ---------------------------------------------------------------------------

export type ClaveFamilia = "baseline" | "ar1" | "volumen" | "gbm";

export type FamiliaModelo = {
  clave: ClaveFamilia;
  /** Nombre corto para la interfaz. */
  etiqueta: string;
  /** Qué es, en una línea. */
  descripcion: string;
  /** MAE walk-forward agregado, en S/ por kg. */
  mae: number;
  /**
   * % de error MENOS que el baseline. Negativo = peor que el baseline.
   * El baseline no participó en seleccionar a ninguna familia, así que esta
   * comparación sí significa algo.
   */
  mejoraVsBaselinePct: number;
};

export type ComparacionHonesta = {
  horizonte: number;
  nProductos: number;
  /** Familias evaluadas, de MENOR a MAYOR error. */
  familias: FamiliaModelo[];
  ganador: FamiliaModelo;
  /** El GBM, para poder contrastarlo con el ganador. null si no se evaluó. */
  gbm: FamiliaModelo | null;
  /** ¿El modelo más complejo es además el mejor? */
  gbmGana: boolean;
};

const ETIQUETAS: Record<ClaveFamilia, { etiqueta: string; descripcion: string }> = {
  baseline: {
    etiqueta: "Baseline",
    descripcion: "el precio de hace una semana, o la media móvil con su pendiente",
  },
  ar1: {
    etiqueta: "AR(1) lineal",
    descripcion: "una recta por mínimos cuadrados sobre el precio del día anterior",
  },
  volumen: {
    etiqueta: "AR(1) + volumen",
    descripcion: "la misma recta, más el volumen de ingreso rezagado",
  },
  gbm: {
    etiqueta: "GBM",
    descripcion: "gradient boosting con lags, día de semana, mes y feriados de Perú",
  },
};

/**
 * Ranking de familias de modelo para un horizonte, derivado del snapshot.
 *
 * Por qué existe: el dashboard mostraba "el modelo le gana al baseline en X de N
 * productos", contando productos donde `forecast.metodo === "gbm" &&
 * mae_modelo < mae_baseline`. Esa condición es imposible de fallar —
 * `forecast_producto` ELIGE el método como el argmin del MAE sobre un conjunto
 * que ya incluye al baseline, así que `mae_modelo <= mae_baseline` para el 100%
 * de los productos por construcción. Medía la selección, no la capacidad.
 *
 * Esta función usa `comparacion_modelos`, donde cada familia se evalúa por
 * separado sobre el mismo conjunto de productos. Devuelve null si el snapshot no
 * la trae (snapshots anteriores al bloque de comparación).
 */
export function comparacionHonesta(
  killGate: KillGate | undefined,
  horizonte = 1,
): ComparacionHonesta | null {
  const h = killGate?.comparacion_modelos?.por_horizonte?.[String(horizonte)];
  if (!h) return null;

  const base = h.mae_baseline;
  if (base == null || base <= 0) return null;

  const crudas: [ClaveFamilia, number | null][] = [
    ["baseline", h.mae_baseline],
    ["ar1", h.mae_ar1],
    ["volumen", h.mae_volumen],
    ["gbm", h.mae_gbm],
  ];

  const familias: FamiliaModelo[] = crudas
    .filter((par): par is [ClaveFamilia, number] => par[1] != null)
    .map(([clave, mae]) => ({
      clave,
      ...ETIQUETAS[clave],
      mae,
      mejoraVsBaselinePct: ((base - mae) / base) * 100,
    }))
    .sort((a, b) => a.mae - b.mae);

  if (!familias.length) return null;

  const ganador = familias[0];
  const gbm = familias.find((f) => f.clave === "gbm") ?? null;

  return {
    horizonte,
    nProductos: h.n_productos,
    familias,
    ganador,
    gbm,
    gbmGana: ganador.clave === "gbm",
  };
}

/** Mercados adicionales al GMML con al menos un precio publicable (puro). */
export function mercadosExtra(s: Snapshot): MercadoExtra[] {
  return (s.mercados ?? []).filter((m) =>
    m.productos.some((p) => p.precio_kg != null),
  );
}

/**
 * Resultados del contraste SISAP ordenados para mostrar: primero los que SÍ
 * tienen ambos precios (por |delta| descendente — lo más divergente arriba),
 * después los "sin contraparte". Puro y server/cliente-safe.
 */
export function resultadosVerificacion(v: Verificacion): VerificacionItem[] {
  const conAmbos = v.resultados.filter((r) => r.gmml_kg != null);
  const sinPar = v.resultados.filter((r) => r.gmml_kg == null);
  conAmbos.sort((a, b) => Math.abs(b.delta_pct ?? 0) - Math.abs(a.delta_pct ?? 0));
  return [...conAmbos, ...sinPar];
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

/**
 * Reduce una serie a como mucho `max` puntos, conservando su FORMA.
 *
 * Muestreo uniforme y no "los últimos N": quedarse con la cola escondería
 * justo el pico viejo que una comparación a dos años quiere mostrar. El último
 * punto se preserva siempre — es el dato vigente y no puede perderse por un
 * redondeo del paso. Mismo criterio que `mcp_server.serie_historica`.
 */
export function submuestrear(series: Point[], max: number): Point[] {
  if (series.length <= max) return series;
  const paso = Math.ceil(series.length / max);
  const out = series.filter((_, i) => i % paso === 0);
  const ultimo = series[series.length - 1];
  if (out[out.length - 1] !== ultimo) out.push(ultimo);
  return out;
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
