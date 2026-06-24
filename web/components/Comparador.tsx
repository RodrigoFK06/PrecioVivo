"use client";

import { useMemo, useState } from "react";
import {
  buscarProductos,
  serieAlineada,
  productosToCSV,
  type Producto,
} from "@/lib/data";
import { soles, fechaCorta } from "@/lib/format";

// Paleta de líneas: tonos distinguibles entre sí, neutros respecto al tema
// (el rose/emerald del tema codifica "más caro / más barato", no identidad).
const COLORES = ["#0ea5e9", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6"];

const MIN_SEL = 2;
const MAX_SEL = 5;

type Rango = { label: string; dias?: number };
const RANGOS: Rango[] = [
  { label: "90 días", dias: 90 },
  { label: "180 días", dias: 180 },
  { label: "1 año", dias: 365 },
  { label: "Todo", dias: undefined },
];

export default function Comparador({ productos }: { productos: Producto[] }) {
  const [query, setQuery] = useState("");
  const [seleccion, setSeleccion] = useState<string[]>(() =>
    productos.slice(0, 2).map((p) => p.slug),
  );
  const [rangoIdx, setRangoIdx] = useState(2); // 1 año por defecto

  const porSlug = useMemo(() => {
    const m = new Map<string, Producto>();
    for (const p of productos) m.set(p.slug, p);
    return m;
  }, [productos]);

  const candidatos = useMemo(
    () => buscarProductos(productos, query),
    [productos, query],
  );

  const seleccionados = useMemo(
    () => seleccion.map((s) => porSlug.get(s)).filter((p): p is Producto => p != null),
    [seleccion, porSlug],
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
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Cabecera */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-slate-200 bg-slate-50/60">
        <div>
          <h2 className="text-sm font-semibold">Comparador de precios</h2>
          <p className="text-xs text-slate-500">
            Elige entre {MIN_SEL} y {MAX_SEL} productos y mira su precio (S/ por
            kg) superpuesto.
          </p>
        </div>
        <button
          type="button"
          onClick={descargarCSV}
          disabled={seleccionados.length === 0}
          className="shrink-0 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:border-emerald-300 hover:text-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
          <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
            <span aria-live="polite">
              {seleccion.length} de {MAX_SEL} elegidos
            </span>
            {lleno && <span className="text-amber-600">Máximo alcanzado</span>}
          </div>

          <ul
            className="mt-2 max-h-64 overflow-y-auto rounded-md border border-slate-100 divide-y divide-slate-50"
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
                        ? "bg-emerald-50/70 text-slate-800"
                        : "hover:bg-slate-50 text-slate-600"
                    } ${deshabilitado ? "cursor-not-allowed opacity-40" : ""}`}
                  >
                    <span
                      aria-hidden
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border"
                      style={
                        checked
                          ? { backgroundColor: colorDe(p.slug), borderColor: colorDe(p.slug) }
                          : { borderColor: "#cbd5e1" }
                      }
                    />
                    <span className="flex-1 truncate">{p.nombre}</span>
                    <span className="shrink-0 tabular-nums text-xs text-slate-400">
                      {soles(p.latest.precio_kg)}
                    </span>
                  </button>
                </li>
              );
            })}
            {candidatos.length === 0 && (
              <li className="px-2.5 py-6 text-center text-xs text-slate-400">
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
                  <span className="text-xs text-slate-600">{p.nombre}</span>
                  <span className="text-xs tabular-nums text-slate-400">
                    {soles(p.latest.precio_kg)}
                  </span>
                </li>
              ))}
              {seleccionados.length < MIN_SEL && (
                <li className="text-xs text-slate-400">
                  Elige al menos {MIN_SEL} productos para comparar.
                </li>
              )}
            </ul>

            <div className="flex shrink-0 rounded-md border border-slate-200 p-0.5">
              {RANGOS.map((r, i) => (
                <button
                  key={r.label}
                  type="button"
                  onClick={() => setRangoIdx(i)}
                  aria-pressed={rangoIdx === i}
                  className={`rounded px-2 py-1 text-xs transition-colors ${
                    rangoIdx === i
                      ? "bg-slate-800 text-white"
                      : "text-slate-500 hover:text-slate-700"
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
          />

          <p className="mt-3 text-[11px] text-slate-400">
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
}: {
  productos: Producto[];
  dias?: number;
  colorDe: (slug: string) => string;
}) {
  const { slugs, filas } = useMemo(
    () => serieAlineada(productos, dias),
    [productos, dias],
  );

  if (productos.length < MIN_SEL) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-400">
        Selecciona {MIN_SEL} o más productos.
      </div>
    );
  }
  if (filas.length < 2) {
    return (
      <div className="flex h-[280px] items-center justify-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-400">
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
      <div className="flex h-[280px] items-center justify-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-400">
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
            stroke="#e2e8f0"
            strokeWidth={1}
          />
          <text
            x={padL - 6}
            y={y(v) + 3}
            textAnchor="end"
            className="fill-slate-400"
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
          className="fill-slate-400"
          fontSize="10"
        >
          {filas[ti] ? fechaCorta(filas[ti].fecha) : ""}
        </text>
      ))}
    </svg>
  );
}
