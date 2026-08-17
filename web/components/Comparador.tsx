"use client";

import { useEffect, useMemo, useState } from "react";
import {
  buscarProductos,
  serieAlineada,
  productosToCSV,
  type Point,
  type Producto,
} from "@/lib/data";
import { soles, fechaCorta } from "@/lib/format";

// Paleta de líneas: tonos distinguibles entre sí, neutros respecto al tema
// (el rojo/verde del tema codifica "más caro / más barato", no identidad de serie).
const COLORES = ["#1f6f8b", "#b06a16", "#6b4a8f", "#a83a5b", "#2d7d6a"];

const MIN_SEL = 2;
const MAX_SEL = 5;

type Rango = { label: string; dias?: number };
const RANGOS: Rango[] = [
  { label: "90 días", dias: 90 },
  { label: "180 días", dias: 180 },
  { label: "1 año", dias: 365 },
  { label: "Todo", dias: undefined },
];

/**
 * `productos` llega SIN series: solo nombre y slug.
 *
 * Antes recibía los 72 con 140 puntos cada uno, serializados en el HTML de la
 * portada — 0,86 MB en cada visita para una herramienta bajo el pliegue que
 * grafica como mucho 5 productos. Ahora la serie de cada uno se pide a
 * `/series/[slug]` (fichero estático, ~24 KB) cuando se selecciona, y se
 * memoriza para no volver a pedirla.
 */
export default function Comparador({ productos }: { productos: Producto[] }) {
  const [query, setQuery] = useState("");
  const [seleccion, setSeleccion] = useState<string[]>(() =>
    productos.slice(0, 2).map((p) => p.slug),
  );
  const [rangoIdx, setRangoIdx] = useState(2); // 1 año por defecto
  const [series, setSeries] = useState<Record<string, Point[]>>({});
  // `cargando` se DERIVA, no se guarda: es exactamente "hay algún seleccionado
  // cuya serie todavía no llegó". Como estado habría que sincronizarlo a mano
  // desde el efecto —lo que además dispara un render extra y `eslint` lo marca—
  // y podría desincronizarse. Derivado no puede.
  const cargando = seleccion.some((s) => !(s in series));

  const porSlug = useMemo(() => {
    const m = new Map<string, Producto>();
    for (const p of productos) m.set(p.slug, p);
    return m;
  }, [productos]);

  const candidatos = useMemo(
    () => buscarProductos(productos, query),
    [productos, query],
  );

  // Trae las series que falten de los seleccionados. Un fallo de red deja el
  // producto sin serie y el gráfico lo omite: es una degradación visible, no
  // una excepción que tumbe el componente entero.
  useEffect(() => {
    const faltan = seleccion.filter((s) => !(s in series));
    if (!faltan.length) return;
    let vigente = true;
    Promise.all(
      faltan.map((slug) =>
        fetch(`/series/${slug}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => [slug, (d?.series ?? []) as Point[]] as const)
          .catch(() => [slug, [] as Point[]] as const),
      ),
    ).then((pares) => {
      if (!vigente) return;
      // Se registra la clave AUNQUE la serie venga vacía (fallo de red): así
      // `cargando` deja de ser true y no se reintenta en bucle.
      setSeries((prev) => ({ ...prev, ...Object.fromEntries(pares) }));
    });
    return () => {
      vigente = false;
    };
  }, [seleccion, series]);

  const seleccionados = useMemo(
    () =>
      seleccion
        .map((s) => {
          const p = porSlug.get(s);
          // Sin serie todavía, el producto no entra al gráfico: `serieAlineada`
          // lo pintaría como una línea vacía y parecería un dato faltante.
          return p && series[s]?.length ? { ...p, series: series[s] } : null;
        })
        .filter((p): p is Producto => p != null),
    [seleccion, porSlug, series],
  );

  const lleno = seleccion.length >= MAX_SEL;

  function toggle(slug: string) {
    setSeleccion((prev) => {
      if (prev.includes(slug)) return prev.filter((s) => s !== slug);
      if (prev.length >= MAX_SEL) return prev;
      return [...prev, slug];
    });
  }

  function descargarCSV() {
    if (seleccionados.length === 0) return;
    const csv = productosToCSV(seleccionados, "series");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `comparador-${seleccionados.map((p) => p.slug).join("-")}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  const colorDe = (slug: string) => {
    const i = seleccion.indexOf(slug);
    return COLORES[(i < 0 ? 0 : i) % COLORES.length];
  };

  return (
    <div className="rounded-sm border border-rule bg-card overflow-hidden">
      {/* Cabecera */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-rule bg-ink/[0.025]">
        <div>
          <h2 className="font-serif text-base text-ink">Comparador de precios</h2>
          <p className="text-xs text-muted">
            Elige entre {MIN_SEL} y {MAX_SEL} productos y mira su precio (S/ por
            kg) superpuesto.
          </p>
        </div>
        <button
          type="button"
          onClick={descargarCSV}
          disabled={seleccionados.length === 0}
          className="shrink-0 rounded-sm border border-rule bg-card px-3 py-1.5 text-xs font-medium text-ink hover:border-ink disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Descargar CSV
        </button>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[260px_1fr]">
        {/* Selector */}
        <div className="flex flex-col">
          <label htmlFor="comparador-buscar" className="sr-only">
            Buscar producto para comparar
          </label>
          <input
            id="comparador-buscar"
            type="search"
            inputMode="search"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar producto…"
            className="rounded-sm border border-rule bg-card px-3 py-1.5 text-sm placeholder:text-faint focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <div className="mt-1 flex items-center justify-between text-[11px] text-faint">
            <span aria-live="polite">
              {seleccion.length} de {MAX_SEL} elegidos
            </span>
            {lleno && <span className="text-muted">Máximo alcanzado</span>}
          </div>

          <ul
            className="mt-2 max-h-64 overflow-y-auto rounded-sm border border-rule divide-y divide-rule"
            role="listbox"
            aria-label="Productos para comparar"
            aria-multiselectable="true"
          >
            {candidatos.map((p) => {
              const checked = seleccion.includes(p.slug);
              const deshabilitado = !checked && lleno;
              return (
                <li key={p.slug}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={checked}
                    onClick={() => toggle(p.slug)}
                    disabled={deshabilitado}
                    className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm transition-colors ${
                      checked
                        ? "bg-accent/[0.07] text-ink"
                        : "hover:bg-ink/[0.04] text-muted"
                    } ${deshabilitado ? "cursor-not-allowed opacity-40" : ""}`}
                  >
                    <span
                      aria-hidden
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border"
                      style={
                        checked
                          ? { backgroundColor: colorDe(p.slug), borderColor: colorDe(p.slug) }
                          : { borderColor: "#cbc4b1" }
                      }
                    />
                    <span className="flex-1 truncate">{p.nombre}</span>
                    <span className="shrink-0 tabular-nums text-xs text-faint">
                      {soles(p.latest.precio_kg)}
                    </span>
                  </button>
                </li>
              );
            })}
            {candidatos.length === 0 && (
              <li className="px-2.5 py-6 text-center text-xs text-faint">
                Sin coincidencias para «{query}».
              </li>
            )}
          </ul>
        </div>

        {/* Gráfico + leyenda */}
        <div className="flex flex-col">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <ul className="flex flex-wrap gap-x-4 gap-y-1.5">
              {seleccionados.map((p) => (
                <li key={p.slug} className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-3.5 rounded-sm"
                    style={{ backgroundColor: colorDe(p.slug) }}
                  />
                  <span className="text-xs text-muted">{p.nombre}</span>
                  <span className="text-xs tabular-nums text-faint">
                    {soles(p.latest.precio_kg)}
                  </span>
                </li>
              ))}
              {seleccionados.length < MIN_SEL && (
                // Se distingue "faltan por elegir" de "faltan por llegar": con
                // carga diferida, al montar hay 2 seleccionados pero sus series
                // todavía viajan, y decir "elige al menos 2" sería mentir sobre
                // lo que el usuario tiene que hacer.
                <li className="text-xs text-faint">
                  {cargando || seleccion.length >= MIN_SEL
                    ? "Cargando series…"
                    : `Elige al menos ${MIN_SEL} productos para comparar.`}
                </li>
              )}
            </ul>

            <div className="flex shrink-0 rounded-sm border border-rule p-0.5">
              {RANGOS.map((r, i) => (
                <button
                  key={r.label}
                  type="button"
                  onClick={() => setRangoIdx(i)}
                  aria-pressed={rangoIdx === i}
                  className={`rounded-sm px-2 py-1 text-xs transition-colors ${
                    rangoIdx === i
                      ? "bg-ink text-paper"
                      : "text-muted hover:text-ink"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>

          <ComparadorChart
            productos={seleccionados}
            dias={RANGOS[rangoIdx].dias}
            colorDe={colorDe}
            cargando={cargando || seleccion.length > seleccionados.length}
          />

          <p className="mt-3 text-[11px] text-faint">
            Precio mayorista por kg. Cada serie tiene su propio color; las fechas
            sin dato se interrumpen. Referencial, no asesoría de compra.
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gráfico SVG puro: varias series de precio superpuestas sobre eje común.
// ---------------------------------------------------------------------------

function ComparadorChart({
  productos,
  dias,
  colorDe,
  cargando = false,
}: {
  productos: Producto[];
  dias?: number;
  colorDe: (slug: string) => string;
  /** Hay series en camino: el hueco es temporal, no una acción pendiente. */
  cargando?: boolean;
}) {
  const { slugs, filas } = useMemo(
    () => serieAlineada(productos, dias),
    [productos, dias],
  );

  if (productos.length < MIN_SEL) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-sm border border-dashed border-rule text-sm text-faint">
        {cargando ? "Cargando series…" : `Selecciona ${MIN_SEL} o más productos.`}
      </div>
    );
  }
  if (filas.length < 2) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-sm border border-dashed border-rule text-sm text-faint">
        Sin datos suficientes para graficar.
      </div>
    );
  }

  const W = 760;
  const H = 300;
  const padL = 46;
  const padR = 14;
  const padT = 14;
  const plotH = 240;

  const n = filas.length - 1;

  // Rango de precios sobre todas las series visibles.
  let min = Infinity;
  let max = -Infinity;
  for (const fila of filas) {
    for (const slug of slugs) {
      const v = fila.precios[slug];
      if (v != null) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
  }
  if (!isFinite(min) || !isFinite(max)) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-sm border border-dashed border-rule text-sm text-faint">
        Sin datos de precio en este rango.
      </div>
    );
  }
  const pad = (max - min) * 0.12 || 0.5;
  const lo = Math.max(0, min - pad);
  const hi = max + pad;

  const x = (i: number) => padL + (i / n) * (W - padL - padR);
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * plotH;

  // Construye un path por serie, partiendo el trazo en huecos (null).
  function pathDe(slug: string): string {
    let d = "";
    let abierto = false;
    filas.forEach((fila, i) => {
      const v = fila.precios[slug];
      if (v == null) {
        abierto = false;
        return;
      }
      d += `${abierto ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      abierto = true;
    });
    return d.trim();
  }

  const gridVals = [hi, (hi + lo) / 2, lo];
  const ticks = Array.from({ length: 6 }, (_, k) => Math.round((k / 5) * n));

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label={`Comparación de precios de ${productos
        .map((p) => p.nombre)
        .join(", ")}`}
    >
      {/* líneas de cuadrícula + etiquetas de precio */}
      {gridVals.map((v, k) => (
        <g key={k}>
          <line
            x1={padL}
            y1={y(v)}
            x2={W - padR}
            y2={y(v)}
            stroke="#e2dccd"
            strokeWidth={1}
          />
          <text
            x={padL - 6}
            y={y(v) + 3}
            textAnchor="end"
            className="fill-faint"
            fontSize="10"
          >
            {v.toFixed(2)}
          </text>
        </g>
      ))}

      {/* una línea por producto */}
      {slugs.map((slug) => (
        <path
          key={slug}
          d={pathDe(slug)}
          fill="none"
          stroke={colorDe(slug)}
          strokeWidth={1.8}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}

      {/* ticks de fecha */}
      {ticks.map((ti, k) => (
        <text
          key={k}
          x={x(ti)}
          y={H - 4}
          textAnchor="middle"
          className="fill-faint"
          fontSize="10"
        >
          {filas[ti] ? fechaCorta(filas[ti].fecha) : ""}
        </text>
      ))}
    </svg>
  );
}
