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
import Guia from "@/components/Guia";
import Link from "next/link";

export const dynamic = "force-static";

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

function Stat({
  figure,
  label,
  accent,
}: {
  figure: React.ReactNode;
  label: string;
  accent?: boolean;
}) {
  return (
    <div className="px-4 py-4 sm:px-5">
      <div
        className={`text-3xl sm:text-4xl font-semibold tracking-tight leading-none tabular-nums ${
          accent ? "text-down" : "text-ink"
        }`}
      >
        {figure}
      </div>
      <div className="eyebrow mt-2">{label}</div>
    </div>
  );
}

function Seccion({
  titulo,
  bajada,
  children,
}: {
  titulo: string;
  bajada?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-16">
      <div className="border-t border-rule-strong pt-4 mb-6">
        <h2 className="text-2xl sm:text-3xl font-semibold tracking-tight text-ink">{titulo}</h2>
        {bajada && (
          <p className="mt-1.5 text-[15px] text-muted max-w-2xl leading-relaxed">{bajada}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function ListaMovers({
  titulo,
  items,
  kind,
}: {
  titulo: string;
  items: Producto[];
  kind: "precio" | "volumen";
}) {
  return (
    <div>
      <h3 className="eyebrow border-b border-rule pb-2 mb-1">{titulo}</h3>
      <ul className="divide-y divide-rule">
        {items.map((p) => (
          <li key={p.slug}>
            <Link
              href={`/p/${p.slug}`}
              className="group flex items-baseline justify-between gap-2 py-2"
            >
              <span className="truncate text-[15px] text-ink group-hover:text-accent transition-colors">
                {p.nombre}
              </span>
              {kind === "precio" ? (
                <span className="flex items-baseline gap-2 shrink-0 tabular-nums">
                  <span className="text-sm text-muted">{soles(p.latest.precio_kg)}</span>
                  <span className={`text-sm font-medium rounded px-1.5 py-0.5 ${moveBg(p.latest.var_pct)}`}>
                    {pct(p.latest.var_pct)}
                  </span>
                </span>
              ) : (
                <span className="shrink-0 text-sm font-medium tabular-nums text-muted">
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
    <div className="mx-auto max-w-6xl px-4 py-10 sm:py-14">
      {/* ── HERO editorial ──────────────────────────────────────────── */}
      <header className="mb-16">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="eyebrow">
            {snap.mercado} · {fechaLarga(snap.latestFecha)}
          </p>
          <FreshnessBadge latestFecha={snap.latestFecha} generatedAt={snap.generatedAt} />
        </div>

        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-[-0.03em] leading-[1.04] text-ink mt-3 max-w-3xl">
          Los precios del mercado, leídos con IA
        </h1>
        <p className="mt-4 text-lg text-muted max-w-2xl leading-relaxed">
          Predecimos los precios mayoristas del Gran Mercado de Lima — y el modelo ya le gana al
          baseline en{" "}
          <span className="text-ink font-medium tabular-nums">
            {modeloIA.iaGana} de {modeloIA.conForecast}
          </span>{" "}
          productos, con{" "}
          <span className="text-down font-medium tabular-nums">
            ~{Math.round(modeloIA.mejoraMediaPct)}% menos error
          </span>
          . Lo probamos en público — y decimos también qué no funciona.
        </p>

        {/* tira de cifras (newspaper "by the numbers") */}
        <div
          data-guia="cifras"
          className="mt-9 grid grid-cols-2 sm:grid-cols-4 border-y-2 border-ink/85 divide-x divide-rule"
        >
          <Stat accent figure={`${modeloIA.iaGana}/${modeloIA.conForecast}`} label="IA le gana al baseline" />
          <Stat accent figure={`~${Math.round(modeloIA.mejoraMediaPct)}%`} label="Menos error vs baseline" />
          <Stat figure={snap.productCount} label={`Productos · ${snap.fechas.length} días`} />
          <Stat
            figure={
              <span>
                <span className="text-up">{nUp}</span>
                <span className="text-faint text-2xl">↑ </span>
                <span className="text-down">{nDown}</span>
                <span className="text-faint text-2xl">↓</span>
              </span>
            }
            label="Subieron / bajaron hoy"
          />
        </div>
      </header>

      {/* ── HOY ─────────────────────────────────────────────────────── */}
      <Seccion
        titulo="El día de hoy"
        bajada="Resumen automático del mercado y consulta en lenguaje natural sobre los precios."
      >
        <div className="grid gap-5 lg:grid-cols-2 items-start">
          <div data-guia="resumen">
            <AISummary resumen={snap.resumenIA} />
          </div>
          <div data-guia="consulta">
            <ConsultaBox />
          </div>
        </div>
      </Seccion>

      {/* ── MOVIMIENTOS ─────────────────────────────────────────────── */}
      <Seccion
        titulo="Movimientos del día"
        bajada="Las mayores variaciones de precio frente a ayer y dónde entró más volumen al mercado."
      >
        <div data-guia="movers" className="grid gap-8 md:grid-cols-3">
          <ListaMovers titulo="Más caros hoy" items={up} kind="precio" />
          <ListaMovers titulo="Más baratos hoy" items={down} kind="precio" />
          <ListaMovers titulo="Mayor ingreso hoy" items={surge} kind="volumen" />
        </div>
      </Seccion>

      {/* ── PRODUCTOS ───────────────────────────────────────────────── */}
      <Seccion
        titulo="Todos los productos"
        bajada={`${snap.productCount} productos en S/ por kg — variación, tendencia, ingreso del día y predicción con IA. Busca o desplázate dentro de la tabla.`}
      >
        <div data-guia="tabla">
          <ProductTable productos={productos} />
        </div>
      </Seccion>

      {/* ── COMPARADOR ──────────────────────────────────────────────── */}
      <Seccion
        titulo="Comparar productos"
        bajada="Superpone la evolución de precio de varios productos a lo largo del tiempo."
      >
        <Comparador productos={snap.productos} />
      </Seccion>

      {/* ── ALERTAS ─────────────────────────────────────────────────── */}
      <Seccion
        titulo="Alertas del día"
        bajada="Variaciones fuertes y anomalías estadísticas sobre el último día. Marca tu watchlist."
      >
        <AlertasWatchlist alertas={alertas} productos={snap.productos} />
      </Seccion>

      {/* ── METODOLOGÍA + DATOS ─────────────────────────────────────── */}
      <Seccion
        titulo="Metodología y datos"
        bajada="Qué funciona, qué no, y los datos crudos para descargar."
      >
        <div data-guia="metodologia">
          <KillGateNote killGate={snap.forecastMeta?.kill_gate} modeloIA={modeloIA} />
        </div>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-5">
          <p className="text-sm text-muted">
            Descarga los precios de hoy o la serie histórica completa (CSV).
          </p>
          <ExportCSV productos={snap.productos} />
        </div>
      </Seccion>

      <Guia />
    </div>
  );
}
