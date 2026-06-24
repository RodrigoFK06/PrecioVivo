import { getSnapshot, movers, supplySurges, type Producto } from "@/lib/data";
import { soles, pct, tons, fechaLarga, moveBg } from "@/lib/format";
import AISummary from "@/components/AISummary";
import ConsultaBox from "@/components/ConsultaBox";
import KillGateNote, { type ModeloIAResumen } from "@/components/KillGateNote";
import FreshnessBadge from "@/components/FreshnessBadge";
import ProductTable from "@/components/ProductTable";
import Comparador from "@/components/Comparador";
import AlertasWatchlist, { type Alerta } from "@/components/AlertasWatchlist";
import ExportCSV from "@/components/ExportCSV";
import Link from "next/link";

export const dynamic = "force-static";

/** Resumen del modelo de IA frente al baseline, derivado de los forecasts por producto. */
function resumenModeloIA(productos: Producto[]): ModeloIAResumen {
  const conForecast = productos.filter((p) => p.forecast).length;
  const ganadores = productos.filter(
    (p) => p.forecast?.metodo === "gbm" && p.forecast.mae_modelo < p.forecast.mae_baseline,
  );
  const mejoras = ganadores.map(
    (p) => ((p.forecast!.mae_baseline - p.forecast!.mae_modelo) / p.forecast!.mae_baseline) * 100,
  );
  const mejoraMediaPct =
    mejoras.length > 0 ? mejoras.reduce((a, b) => a + b, 0) / mejoras.length : 0;
  return { conForecast, iaGana: ganadores.length, mejoraMediaPct };
}

function Kpi({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border bg-white p-4 shadow-sm ${
        accent ? "border-emerald-200 ring-1 ring-inset ring-emerald-50" : "border-slate-200"
      }`}
    >
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div
        className={`mt-1 text-2xl sm:text-3xl font-bold tracking-tight tabular-nums ${
          accent ? "text-emerald-600" : "text-slate-900"
        }`}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-slate-500 leading-snug">{sub}</div>}
    </div>
  );
}

function SectionHeader({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-lg sm:text-xl font-semibold tracking-tight text-slate-900">{title}</h2>
      {desc && <p className="mt-0.5 text-sm text-slate-500">{desc}</p>}
    </div>
  );
}

function MoverCard({
  title,
  accent,
  items,
  kind,
}: {
  title: string;
  accent: string;
  items: Producto[];
  kind: "precio" | "volumen";
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className={`text-sm font-semibold mb-2.5 ${accent}`}>{title}</h3>
      <ul className="divide-y divide-slate-50">
        {items.map((p) => (
          <li key={p.slug}>
            <Link
              href={`/p/${p.slug}`}
              className="group flex items-center justify-between gap-2 py-1.5 hover:text-slate-900"
            >
              <span className="truncate text-sm text-slate-600 group-hover:text-emerald-700">
                {p.nombre}
              </span>
              {kind === "precio" ? (
                <span className="flex items-center gap-2 shrink-0">
                  <span className="text-sm tabular-nums text-slate-500">{soles(p.latest.precio_kg)}</span>
                  <span
                    className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${moveBg(
                      p.latest.var_pct,
                    )}`}
                  >
                    {pct(p.latest.var_pct)}
                  </span>
                </span>
              ) : (
                <span className="shrink-0 text-sm tabular-nums font-medium text-sky-700">
                  {tons(p.latest.masa_hoy)}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default async function Home() {
  const snap = await getSnapshot();
  const up = movers(snap, "up", 5);
  const down = movers(snap, "down", 5);
  const surge = supplySurges(snap, 5);
  const productos = [...snap.productos].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  const modeloIA = resumenModeloIA(snap.productos);
  const alertas = ((snap as unknown as { alertas?: Alerta[] }).alertas ?? []) as Alerta[];
  const nUp = snap.productos.filter((p) => (p.latest.var_pct ?? 0) > 0).length;
  const nDown = snap.productos.filter((p) => (p.latest.var_pct ?? 0) < 0).length;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:py-10">
      {/* ── HERO: foco + KPIs ───────────────────────────────────────── */}
      <header className="mb-12">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
            Precios mayoristas, en vivo
          </h1>
          <FreshnessBadge latestFecha={snap.latestFecha} generatedAt={snap.generatedAt} />
        </div>
        <p className="mt-2 text-[15px] text-slate-500 max-w-2xl">
          {snap.mercado} · {fechaLarga(snap.latestFecha)}.{" "}
          <span className="text-slate-700">
            Predecimos los precios con IA — y ya le ganamos al baseline para la mayoría.
          </span>
        </p>

        <div className="mt-5 grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Kpi
            accent
            label="IA le gana al baseline"
            value={`${modeloIA.iaGana}/${modeloIA.conForecast}`}
            sub="productos · validado walk-forward"
          />
          <Kpi
            accent
            label="Menos error vs baseline"
            value={`~${Math.round(modeloIA.mejoraMediaPct)}%`}
            sub="en esos productos"
          />
          <Kpi
            label="Cobertura"
            value={snap.productCount}
            sub={`productos · ${snap.fechas.length} días de historia`}
          />
          <Kpi
            label="Movimiento de hoy"
            value={
              <span>
                <span className="text-rose-600">{nUp}↑</span>{" "}
                <span className="text-slate-300 text-xl">/</span>{" "}
                <span className="text-emerald-600">{nDown}↓</span>
              </span>
            }
            sub="subieron / bajaron vs ayer"
          />
        </div>
      </header>

      {/* ── HOY: resumen IA + consulta ──────────────────────────────── */}
      <section className="mb-12">
        <SectionHeader title="El día de hoy" desc="Resumen automático y consulta en lenguaje natural." />
        <div className="grid gap-4 lg:grid-cols-2 items-start">
          <AISummary resumen={snap.resumenIA} />
          <ConsultaBox />
        </div>
      </section>

      {/* ── MOVIMIENTOS: 3-up balanceado ────────────────────────────── */}
      <section className="mb-12">
        <SectionHeader title="Movimientos del día" desc="Qué subió, qué bajó y dónde entró más volumen." />
        <div className="grid gap-4 md:grid-cols-3">
          <MoverCard title="▲ Más caros hoy" accent="text-rose-600" items={up} kind="precio" />
          <MoverCard title="▼ Más baratos hoy" accent="text-emerald-600" items={down} kind="precio" />
          <MoverCard title="● Mayor ingreso hoy" accent="text-sky-600" items={surge} kind="volumen" />
        </div>
      </section>

      {/* ── PRODUCTOS: el dato principal (header + buscador propios, tabla acotada) ── */}
      <section className="mb-12">
        <ProductTable productos={productos} />
      </section>

      {/* ── COMPARADOR: ancho completo, el gráfico necesita espacio ──── */}
      <section className="mb-12">
        <Comparador productos={snap.productos} />
      </section>

      {/* ── ALERTAS: compacto (top 7 + ver todas) ───────────────────── */}
      <section className="mb-12">
        <AlertasWatchlist alertas={alertas} productos={snap.productos} />
      </section>

      {/* ── METODOLOGÍA + DATOS ─────────────────────────────────────── */}
      <section>
        <KillGateNote killGate={snap.forecastMeta?.kill_gate} modeloIA={modeloIA} />
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-sm text-slate-500">
            Descarga los precios de hoy o la serie histórica completa (CSV).
          </p>
          <ExportCSV productos={snap.productos} />
        </div>
      </section>
    </div>
  );
}
