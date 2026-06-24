"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { buscarProductos, type Producto } from "@/lib/data";
import { soles, pct, tons, moveBg, tendenciaClass } from "@/lib/format";
import Sparkline from "@/components/Sparkline";

type SortKey = "nombre" | "precio" | "var" | "ingreso";
type SortDir = "asc" | "desc";

type Column = {
  key: SortKey;
  label: string;
  align: "left" | "right";
  /** Encabezados informativos que no ordenan. */
  sortable: boolean;
};

const COLUMNS: Column[] = [
  { key: "nombre", label: "Producto", align: "left", sortable: true },
  { key: "precio", label: "S/ por kg", align: "right", sortable: true },
  { key: "var", label: "Δ ayer", align: "right", sortable: true },
];

function compareProductos(a: Producto, b: Producto, key: SortKey): number {
  switch (key) {
    case "nombre":
      return a.nombre.localeCompare(b.nombre, "es");
    case "precio":
      return num(a.latest.precio_kg) - num(b.latest.precio_kg);
    case "var":
      return num(a.latest.var_pct) - num(b.latest.var_pct);
    case "ingreso":
      return num(a.latest.masa_hoy) - num(b.latest.masa_hoy);
  }
}

/** Nulos al final en orden ascendente (se invierte con la dirección). */
function num(v: number | null | undefined): number {
  return v == null ? Number.NEGATIVE_INFINITY : v;
}

export default function ProductTable({ productos }: { productos: Producto[] }) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("nombre");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const filtradas = useMemo(() => {
    const base = buscarProductos(productos, query);
    const dir = sortDir === "asc" ? 1 : -1;
    return [...base].sort((a, b) => dir * compareProductos(a, b, sortKey));
  }, [productos, query, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // El nombre arranca ascendente (A-Z); las métricas arrancan descendente.
      setSortDir(key === "nombre" ? "asc" : "desc");
    }
  }

  const sortArrow = (key: SortKey) =>
    key === sortKey ? (sortDir === "asc" ? "▲" : "▼") : "";

  const ariaSort = (key: SortKey): "ascending" | "descending" | "none" =>
    key === sortKey ? (sortDir === "asc" ? "ascending" : "descending") : "none";

  return (
    <div className="rounded-sm border border-rule bg-card overflow-hidden">
      {/* Cabecera con búsqueda en vivo */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-rule bg-ink/[0.025]">
        <h2 className="font-serif text-base text-ink">Todos los productos</h2>
        <div className="flex items-center gap-3">
          <label htmlFor="buscar-producto" className="sr-only">
            Buscar producto
          </label>
          <input
            id="buscar-producto"
            type="search"
            inputMode="search"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar producto…"
            className="w-44 sm:w-56 rounded-sm border border-rule bg-card px-3 py-1.5 text-sm placeholder:text-faint focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <span className="text-xs tabular-nums text-faint" aria-live="polite">
            {filtradas.length} de {productos.length}
          </span>
        </div>
      </div>

      <div className="max-h-[34rem] overflow-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">
            Precios mayoristas por kg, variación frente a ayer, tendencia, ingreso del día y
            pronóstico beta por producto.
          </caption>
          <thead className="sticky top-0 z-10 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
            <tr className="text-left text-xs text-faint border-b border-rule">
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={ariaSort(c.key)}
                  className={`px-4 py-2 font-medium ${c.align === "right" ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort(c.key)}
                    className={`inline-flex items-center gap-1 hover:text-ink ${
                      c.align === "right" ? "flex-row-reverse" : ""
                    } ${c.key === sortKey ? "text-ink" : ""}`}
                  >
                    <span>{c.label}</span>
                    <span aria-hidden className="w-2 text-[9px]">
                      {sortArrow(c.key)}
                    </span>
                  </button>
                </th>
              ))}
              <th scope="col" className="hidden md:table-cell px-4 py-2 font-medium">
                Tendencia
              </th>
              <th scope="col" className="hidden lg:table-cell px-4 py-2 font-medium text-right">
                <button
                  type="button"
                  onClick={() => toggleSort("ingreso")}
                  aria-pressed={sortKey === "ingreso"}
                  className={`inline-flex flex-row-reverse items-center gap-1 hover:text-ink ${
                    sortKey === "ingreso" ? "text-ink" : ""
                  }`}
                >
                  <span>Ingreso hoy</span>
                  <span aria-hidden className="w-2 text-[9px]">
                    {sortArrow("ingreso")}
                  </span>
                </button>
              </th>
              <th scope="col" className="hidden sm:table-cell px-4 py-2 font-medium text-right">
                <span className="inline-flex items-center gap-1.5">
                  Pronóstico
                  <span className="rounded-sm bg-ink/[0.05] px-1 py-0.5 text-[9px] uppercase tracking-wide text-muted">
                    beta
                  </span>
                </span>
              </th>
              <th scope="col" className="hidden lg:table-cell px-4 py-2 font-medium">
                30 días
              </th>
            </tr>
          </thead>
          <tbody>
            {filtradas.map((p) => {
              const fc = p.forecast;
              return (
                <tr key={p.slug} className="border-b border-rule hover:bg-accent/[0.04] transition-colors">
                  <td className="px-4 py-2">
                    <Link
                      href={`/p/${p.slug}`}
                      className="font-medium text-ink hover:text-accent hover:underline"
                    >
                      {p.nombre}
                    </Link>
                    <span className="ml-1.5 text-xs text-faint">
                      /{p.unidad.toLowerCase()}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    {soles(p.latest.precio_kg)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${moveBg(
                        p.latest.var_pct,
                      )}`}
                    >
                      {pct(p.latest.var_pct)}
                    </span>
                  </td>
                  <td className="hidden md:table-cell px-4 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${tendenciaClass(
                        p.latest.tendencia,
                      )}`}
                    >
                      {p.latest.tendencia ?? "—"}
                    </span>
                  </td>
                  <td className="hidden lg:table-cell px-4 py-2 text-right tabular-nums text-muted">
                    {tons(p.latest.masa_hoy)}
                  </td>
                  <td className="hidden sm:table-cell px-4 py-2 text-right tabular-nums text-muted">
                    {fc ? (
                      <span
                        title={`Estimado a ${fc.horizonte_dias} día(s) · método ${fc.metodo} · rango S/ ${fc.intervalo[0].toFixed(
                          2,
                        )}–${fc.intervalo[1].toFixed(2)}`}
                      >
                        {soles(fc.precio_estimado)}
                      </span>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </td>
                  <td className="hidden lg:table-cell px-4 py-2">
                    <Sparkline values={p.series.slice(-30).map((pt) => pt.precio_kg)} />
                  </td>
                </tr>
              );
            })}
            {filtradas.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-faint">
                  No hay productos que coincidan con «{query}».
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
