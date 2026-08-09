"use client";

import { useState } from "react";
import Link from "next/link";
import { soles, pct, moveBg } from "@/lib/format";

type Fila = {
  nombre: string;
  slug: string;
  precio_kg: number | null;
  var_pct: number | null;
  /** Presente solo en productos de otros mercados (sin página /p/[slug]). */
  mercado?: string;
};

type Respuesta = {
  texto: string;
  productos: Fila[];
  fuente?: "llm" | "fallback";
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
    <div className="h-full rounded-sm border border-rule bg-card p-5">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="font-serif text-base text-ink">Pregunta sobre los precios</h2>
        <span className="text-[10px] uppercase tracking-wide rounded-sm bg-ink/[0.05] text-muted px-1.5 py-0.5 font-medium">
          beta
        </span>
      </div>
      <p className="text-xs text-muted mb-3">
        En lenguaje natural. Solo usa los precios reales del último día.
      </p>

      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="p. ej. ¿qué está más barato hoy?"
          aria-label="Pregunta sobre los precios"
          className="flex-1 rounded-sm border border-rule bg-card px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent"
        />
        <button
          type="submit"
          disabled={cargando || !q.trim()}
          className="shrink-0 rounded-sm border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper hover:bg-accent hover:border-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
            className="text-xs rounded-full border border-rule px-2.5 py-1 text-muted hover:border-ink hover:text-ink disabled:opacity-50 transition-colors"
          >
            {ej}
          </button>
        ))}
      </div>

      {error && <p className="mt-4 text-sm text-up">{error}</p>}

      {resp && !error && (
        <div className="mt-4 border-t border-rule pt-4">
          <p className="text-[15px] leading-relaxed text-ink">{resp.texto}</p>

          {resp.productos.length > 0 && (
            <ul className="mt-3 space-y-1">
              {resp.productos.map((p) => {
                const contenido = (
                  <>
                    <span className="truncate text-sm">
                      {p.nombre}
                      {p.mercado && (
                        <span className="ml-1.5 text-xs text-faint">· {p.mercado}</span>
                      )}
                    </span>
                    <span className="flex items-center gap-2 shrink-0">
                      <span className="text-sm tabular-nums text-muted">{soles(p.precio_kg)}</span>
                      <span
                        className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${moveBg(p.var_pct)}`}
                      >
                        {pct(p.var_pct)}
                      </span>
                    </span>
                  </>
                );
                // Los productos de otros mercados no tienen página propia todavía.
                return (
                  <li key={p.slug}>
                    {p.mercado ? (
                      <div className="flex items-center justify-between gap-2 rounded-sm px-2 py-1.5">
                        {contenido}
                      </div>
                    ) : (
                      <Link
                        href={`/p/${p.slug}`}
                        className="flex items-center justify-between gap-2 rounded-sm px-2 py-1.5 hover:bg-ink/[0.04]"
                      >
                        {contenido}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <p className="mt-3 text-[11px] text-faint">
            {resp.fuente === "llm"
              ? "Interpretado con IA sobre el snapshot. Precios reales del mercado."
              : "Respuesta por coincidencia de palabras clave sobre el snapshot."}{" "}
            Referencial, no asesoría de compra.
          </p>
        </div>
      )}
    </div>
  );
}
