"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { productosToCSV, type Producto } from "@/lib/data";
import { pct, fechaCorta } from "@/lib/format";

/**
 * Alerta operativa generada por el pipeline (reglas sobre el último día).
 * No existe en lib/data (capa de Datos), así que se declara localmente.
 *  - `tipo`: clase de regla que la disparó.
 *      · `var_pct`   → variación diaria fuerte (valor = % del día).
 *      · `anomalia`  → desviación estadística (valor = z-score, σ).
 *      · `nivel`     → cruce de umbral de nivel de precio (valor = S//kg).
 *  - `valor`: magnitud asociada a la regla (su unidad depende de `tipo`).
 *  - `regla`: descripción legible de la regla aplicada.
 */
export type Alerta = {
  producto: string;
  slug: string;
  tipo: "var_pct" | "anomalia" | "nivel" | string;
  mensaje: string;
  valor: number;
  fecha: string;
  regla: string;
};

type Props = {
  alertas: Alerta[];
  productos: Producto[];
};

const TIPO_META: Record<
  string,
  { etiqueta: string; clase: string }
> = {
  var_pct: { etiqueta: "Variación", clase: "bg-ink/[0.06] text-muted" },
  anomalia: { etiqueta: "Anomalía", clase: "bg-ink/[0.06] text-muted" },
  nivel: { etiqueta: "Nivel", clase: "bg-ink/[0.06] text-muted" },
};

function metaDe(tipo: string) {
  return TIPO_META[tipo] ?? { etiqueta: tipo, clase: "bg-ink/[0.06] text-muted" };
}

/**
 * Color del badge de `valor`. Para variaciones, más caro = up (rojo) y más barato =
 * down (verde), convención del proyecto. Para anomalías el signo del z es neutro
 * informativo (muted). Resto: muted.
 */
function valorClase(a: Alerta): string {
  if (a.tipo === "var_pct" || a.tipo === "nivel") {
    if (a.valor > 0) return "bg-up/10 text-up";
    if (a.valor < 0) return "bg-down/10 text-down";
    return "bg-ink/[0.06] text-muted";
  }
  return "bg-ink/[0.06] text-muted";
}

/** Texto del badge de `valor` según el tipo (porcentaje, σ o soles). */
function valorTexto(a: Alerta): string {
  if (a.tipo === "var_pct") return pct(a.valor);
  if (a.tipo === "anomalia") return `${a.valor > 0 ? "+" : ""}${a.valor.toFixed(1)}σ`;
  if (a.tipo === "nivel") return `S/ ${a.valor.toFixed(2)}`;
  return String(a.valor);
}

/** Dispara la descarga de un CSV generado en el cliente (sin tocar el disco del server). */
function descargarCSV(csv: string, nombre: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function AlertasWatchlist({ alertas, productos }: Props) {
  // Watchlist: conjunto de slugs marcados por el usuario.
  const [watch, setWatch] = useState<Set<string>>(new Set());
  const [soloWatch, setSoloWatch] = useState(false);
  const [mostrarTodas, setMostrarTodas] = useState(false);
  const [mostrarSelector, setMostrarSelector] = useState(false);
  const LIMITE = 7;

  // Productos que tienen al menos una alerta hoy (los que importan para marcar).
  const productosConAlerta = useMemo(() => {
    const slugs = new Set(alertas.map((a) => a.slug));
    return productos
      .filter((p) => slugs.has(p.slug))
      .sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  }, [alertas, productos]);

  const visibles = useMemo(() => {
    if (!soloWatch || watch.size === 0) return alertas;
    return alertas.filter((a) => watch.has(a.slug));
  }, [alertas, soloWatch, watch]);

  const mostradas = mostrarTodas ? visibles : visibles.slice(0, LIMITE);

  function toggle(slug: string) {
    setWatch((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function exportar() {
    // Si hay watchlist activa, exporta solo esos productos; si no, todos los que
    // tienen alerta. Usa la serializadora pura de lib + Blob en el cliente.
    const elegidos =
      watch.size > 0
        ? productosConAlerta.filter((p) => watch.has(p.slug))
        : productosConAlerta;
    const csv = productosToCSV(elegidos, "latest");
    descargarCSV(csv, "alertas-precio-vivo.csv");
  }

  if (alertas.length === 0) {
    return (
      <div className="rounded-sm border border-rule bg-card p-5">
        <h2 className="font-serif text-base text-ink">Alertas del día</h2>
        <p className="mt-2 text-sm text-faint">
          Sin alertas hoy. No hubo variaciones ni anomalías sobre el umbral.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-sm border border-rule bg-card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-rule bg-ink/[0.025]">
        <div>
          <h2 className="font-serif text-base text-ink">Alertas del día</h2>
          <p className="text-xs text-faint mt-0.5">
            Variaciones fuertes y anomalías sobre el último día.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted select-none">
            <input
              type="checkbox"
              checked={soloWatch}
              onChange={(e) => setSoloWatch(e.target.checked)}
              disabled={watch.size === 0}
              className="h-3.5 w-3.5 rounded-sm border-rule text-accent focus:ring-accent disabled:opacity-40"
            />
            Solo mi watchlist
            {watch.size > 0 && (
              <span className="tabular-nums text-faint">({watch.size})</span>
            )}
          </label>
          <button
            type="button"
            onClick={exportar}
            className="shrink-0 rounded-sm border border-rule bg-card px-2.5 py-1.5 text-xs font-medium text-ink hover:border-ink transition-colors"
          >
            Descargar CSV
          </button>
        </div>
      </div>

      {/* Selector de watchlist: colapsado por defecto para no inflar el panel. */}
      <div className="border-b border-rule px-4 py-2">
        <button
          type="button"
          onClick={() => setMostrarSelector((v) => !v)}
          className="text-[11px] uppercase tracking-wide text-faint hover:text-accent transition-colors"
        >
          {mostrarSelector ? "− Ocultar watchlist" : "+ Marcar watchlist"}{" "}
          <span className="tabular-nums">({productosConAlerta.length})</span>
        </button>
        {mostrarSelector && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {productosConAlerta.map((p) => {
            const activo = watch.has(p.slug);
            return (
              <button
                key={p.slug}
                type="button"
                aria-pressed={activo}
                onClick={() => toggle(p.slug)}
                className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                  activo
                    ? "border-accent/40 bg-accent/[0.08] text-accent"
                    : "border-rule text-muted hover:border-ink hover:text-ink"
                }`}
              >
                <span aria-hidden className="mr-1">
                  {activo ? "✓" : "+"}
                </span>
                {p.nombre}
              </button>
            );
          })}
        </div>
        )}
      </div>

      <ul className="divide-y divide-rule">
        {mostradas.map((a, i) => {
          const meta = metaDe(a.tipo);
          const enWatch = watch.has(a.slug);
          return (
            <li
              key={`${a.slug}-${a.tipo}-${i}`}
              className="flex items-start justify-between gap-3 px-4 py-2.5 hover:bg-ink/[0.03] transition-colors"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Link
                    href={`/p/${a.slug}`}
                    className="truncate text-sm font-medium text-ink hover:text-accent hover:underline"
                  >
                    {a.producto}
                  </Link>
                  {enWatch && (
                    <span className="shrink-0 rounded-sm bg-accent/[0.08] px-1 py-0.5 text-[9px] uppercase tracking-wide text-accent">
                      watch
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-xs text-muted">{a.mensaje}</p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${valorClase(a)}`}
                  title={a.regla}
                >
                  {valorTexto(a)}
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${meta.clase}`}
                  >
                    {meta.etiqueta}
                  </span>
                  <span className="text-[10px] tabular-nums text-faint">
                    {fechaCorta(a.fecha)}
                  </span>
                </span>
              </div>
            </li>
          );
        })}
        {visibles.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-faint">
            Tu watchlist no tiene alertas hoy.
          </li>
        )}
      </ul>
      {visibles.length > LIMITE && (
        <div className="border-t border-rule px-4 py-2 text-center">
          <button
            type="button"
            onClick={() => setMostrarTodas((v) => !v)}
            className="text-xs font-medium text-accent hover:text-ink transition-colors"
          >
            {mostrarTodas ? "Ver menos" : `Ver todas (${visibles.length})`}
          </button>
        </div>
      )}
    </div>
  );
}
