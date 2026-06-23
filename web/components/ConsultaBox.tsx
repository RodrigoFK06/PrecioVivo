"use client";

import { useState } from "react";
import Link from "next/link";
import { soles, pct, moveBg } from "@/lib/format";

type Fila = {
  nombre: string;
  slug: string;
  precio_kg: number | null;
  var_pct: number | null;
};

type Respuesta = {
  texto: string;
  productos: Fila[];
  fuente?: "claude" | "fallback";
};

const EJEMPLOS = ["¿Qué está más barato hoy?", "¿Qué subió de precio?", "maracuyá"];

export default function ConsultaBox() {
  const [q, setQ] = useState("");
  const [cargando, setCargando] = useState(false);
  const [resp, setResp] = useState<Respuesta | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function consultar(pregunta: string) {
    const texto = pregunta.trim();
    if (!texto || cargando) return;
    setCargando(true);
    setError(null);
    try {
      const r = await fetch("/api/consulta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: texto }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as Respuesta;
      setResp(data);
    } catch {
      setError("No se pudo consultar ahora mismo. Intenta de nuevo.");
      setResp(null);
    } finally {
      setCargando(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void consultar(q);
  }

  return (
    <div className="h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-sm font-semibold">Pregunta sobre los precios</h2>
        <span className="text-[10px] uppercase tracking-wide rounded bg-amber-50 text-amber-700 px-1.5 py-0.5 font-medium">
          beta
        </span>
      </div>
      <p className="text-xs text-slate-500 mb-3">
        En lenguaje natural. Solo usa los precios reales del último día.
      </p>

      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="p. ej. ¿qué está más barato hoy?"
          aria-label="Pregunta sobre los precios"
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
        />
        <button
          type="submit"
          disabled={cargando || !q.trim()}
          className="shrink-0 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {cargando ? "Consultando…" : "Preguntar"}
        </button>
      </form>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {EJEMPLOS.map((ej) => (
          <button
            key={ej}
            type="button"
            onClick={() => {
              setQ(ej);
              void consultar(ej);
            }}
            disabled={cargando}
            className="text-xs rounded-full border border-slate-200 px-2.5 py-1 text-slate-500 hover:border-emerald-300 hover:text-emerald-700 disabled:opacity-50 transition-colors"
          >
            {ej}
          </button>
        ))}
      </div>

      {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}

      {resp && !error && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="text-[15px] leading-relaxed text-slate-700">{resp.texto}</p>

          {resp.productos.length > 0 && (
            <ul className="mt-3 space-y-1">
              {resp.productos.map((p) => (
                <li key={p.slug}>
                  <Link
                    href={`/p/${p.slug}`}
                    className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50"
                  >
                    <span className="truncate text-sm">{p.nombre}</span>
                    <span className="flex items-center gap-2 shrink-0">
                      <span className="text-sm tabular-nums text-slate-600">{soles(p.precio_kg)}</span>
                      <span
                        className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${moveBg(p.var_pct)}`}
                      >
                        {pct(p.var_pct)}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <p className="mt-3 text-[11px] text-slate-400">
            {resp.fuente === "claude"
              ? "Interpretado con IA (Claude) sobre el snapshot. Precios reales del mercado."
              : "Respuesta por coincidencia de palabras clave sobre el snapshot."}{" "}
            Referencial, no asesoría de compra.
          </p>
        </div>
      )}
    </div>
  );
}
