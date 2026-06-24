import { getSnapshot, movers, supplySurges, topAnomalias, type Producto } from "@/lib/data";
import { soles, pct, fechaLarga, moveBg } from "@/lib/format";
import AISummary from "@/components/AISummary";
import ConsultaBox from "@/components/ConsultaBox";
import KillGateNote, { type ModeloIAResumen } from "@/components/KillGateNote";
import FreshnessBadge from "@/components/FreshnessBadge";
import ProductTable from "@/components/ProductTable";
import Comparador from "@/components/Comparador";
import AlertasWatchlist, { type Alerta } from "@/components/AlertasWatchlist";
import ExportCSV from "@/components/ExportCSV";
import Link from "next/link";

/** Resumen del modelo de IA frente al baseline, derivado de los forecasts por producto. */
function resumenModeloIA(productos: Producto[]): ModeloIAResumen {
  const conForecast = productos.filter((p) => p.forecast).length;
  const ganadores = productos.filter(
    (p) => p.forecast?.metodo === "gbm" && p.forecast.mae_modelo < p.forecast.mae_baseline,
  );
  const mejoras = ganadores.map(
    (p) =>
      ((p.forecast!.mae_baseline - p.forecast!.mae_modelo) / p.forecast!.mae_baseline) * 100,
  );
  const mejoraMediaPct =
    mejoras.length > 0 ? mejoras.reduce((a, b) => a + b, 0) / mejoras.length : 0;
  return { conForecast, iaGana: ganadores.length, mejoraMediaPct };
}

export const dynamic = "force-static";

function MoverList({ items, dir }: { items: Producto[]; dir: "up" | "down" }) {
  const up = dir === "up";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
        <span className={up ? "text-rose-600" : "text-emerald-600"}>
          {up ? "▲ Más caros hoy" : "▼ Más baratos hoy"}
        </span>
      </h3>
      <ul className="space-y-0.5">
        {items.map((p) => (
          <li key={p.slug}>
            <Link
              href={`/p/${p.slug}`}
              className="group flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50 transition-colors"
            >
              <span className="truncate text-sm text-slate-700 group-hover:text-slate-900">{p.nombre}</span>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-sm tabular-nums text-slate-600">{soles(p.latest.precio_kg)}</span>
                <span className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${moveBg(p.latest.var_pct)}`}>
                  {pct(p.latest.var_pct)}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default async function Home() {
  const snap = await getSnapshot();
  const up = movers(snap, "up", 6);
  const down = movers(snap, "down", 6);
  const productos = [...snap.productos].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  const modeloIA = resumenModeloIA(snap.productos);
  // `alertas` lo produce el pipeline pero aún no está en el tipo Snapshot (capa de Datos).
  const alertas = ((snap as unknown as { alertas?: Alerta[] }).alertas ?? []) as Alerta[];
  // supplySurges/topAnomalias quedan disponibles como señales de oferta/anomalía.
  void supplySurges;
  void topAnomalias;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:py-10">
      {/* hero */}
      <header className="mb-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-slate-900">
            Precios mayoristas, en vivo
          </h1>
          <FreshnessBadge latestFecha={snap.latestFecha} generatedAt={snap.generatedAt} />
        </div>
        <p className="text-slate-500 mt-2 text-[15px]">
          {snap.mercado} · actualizado el{" "}
          <span className="font-medium text-slate-700">{fechaLarga(snap.latestFecha)}</span>
        </p>
        <p className="text-slate-600 mt-3 text-[15px] max-w-2xl leading-relaxed">
          <span className="font-medium text-slate-900">Predecimos los precios mayoristas con IA</span> — y
          ya le ganamos al baseline para la mayoría de productos. Un modelo GBM le saca{" "}
          <span className="font-medium text-emerald-700 tabular-nums">
            ~{Math.round(modeloIA.mejoraMediaPct)}% menos error
          </span>{" "}
          al baseline ingenuo en{" "}
          <span className="font-medium tabular-nums">
            {modeloIA.iaGana} de {modeloIA.conForecast}
          </span>{" "}
          productos, validado walk-forward. Lo probamos en público: el volumen, por ahora, no aporta —
          y también lo decimos.
        </p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-white border border-slate-200 px-3 py-1 tabular-nums shadow-sm">
            {snap.productCount} productos
          </span>
          <span className="rounded-full bg-white border border-slate-200 px-3 py-1 tabular-nums shadow-sm">
            {snap.fechas.length} días de historia
          </span>
          <span className="rounded-full bg-emerald-50 text-emerald-700 px-3 py-1 font-medium ring-1 ring-inset ring-emerald-100 tabular-nums">
            IA le gana al baseline en {modeloIA.iaGana}/{modeloIA.conForecast}
          </span>
          <span className="rounded-full bg-white border border-slate-200 px-3 py-1 font-medium shadow-sm">
            precios en S/ por kg
          </span>
        </div>
      </header>

      {/* resumen IA + consulta en lenguaje natural */}
      <section className="mb-8 grid gap-4 lg:grid-cols-2 items-start">
        <AISummary resumen={snap.resumenIA} />
        <ConsultaBox />
      </section>

      {/* movers + alertas/watchlist (reemplaza el panel de anomalías) */}
      <section className="grid gap-4 mb-10 lg:grid-cols-3">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1 lg:col-span-1">
          <MoverList items={up} dir="up" />
          <MoverList items={down} dir="down" />
        </div>
        <div className="lg:col-span-2">
          <AlertasWatchlist alertas={alertas} productos={snap.productos} />
        </div>
      </section>

      {/* comparador multi-producto */}
      <section className="mb-10">
        <Comparador productos={snap.productos} />
      </section>

      {/* tabla interactiva */}
      <section className="mb-10">
        <ProductTable productos={productos} />
      </section>

      {/* nota metodológica honesta: el modelo de IA gana, el volumen no aporta */}
      <section className="mb-8">
        <KillGateNote killGate={snap.forecastMeta?.kill_gate} modeloIA={modeloIA} />
      </section>

      {/* descarga de datos en CSV */}
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-sm text-slate-500">
          Datos abiertos: descarga los precios de hoy o la serie histórica completa.
        </p>
        <ExportCSV productos={snap.productos} />
      </section>
    </div>
  );
}
