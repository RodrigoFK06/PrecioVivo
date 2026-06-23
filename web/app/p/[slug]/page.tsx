import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { getSnapshot, getProducto, stats } from "@/lib/data";
import { soles, pct, tons, fechaLarga, moveBg, tendenciaClass } from "@/lib/format";
import PriceChart from "@/components/PriceChart";

export const dynamic = "force-static";

export async function generateStaticParams() {
  const snap = await getSnapshot();
  return snap.productos.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const p = await getProducto(slug);
  if (!p) return { title: "Producto no encontrado — Precio Vivo" };
  const precio = soles(p.latest.precio_kg);
  return {
    title: `Precio de ${p.nombre} hoy en Lima — ${precio}/kg | Precio Vivo`,
    description: `Precio mayorista de ${p.nombre} en el Gran Mercado Mayorista de Lima: ${precio} por kg (${pct(
      p.latest.var_pct,
    )} vs ayer). Historial y tendencia.`,
  };
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-base font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export default async function ProductoPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const p = await getProducto(slug);
  if (!p) notFound();

  const st = stats(p.series);

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <Link href="/" className="text-sm text-slate-500 hover:text-emerald-600">
        ← Todos los productos
      </Link>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">{p.nombre}</h1>
          <p className="text-slate-500 mt-1 text-sm">
            {p.unidad} · equiv. {p.equiv_kg ?? "—"} kg · actualizado {fechaLarga(p.latest.fecha)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-3xl font-bold tabular-nums">{soles(p.latest.precio_kg)}</span>
          <span className="text-sm text-slate-400">/kg</span>
          <span className={`text-sm font-medium tabular-nums rounded px-2 py-1 ${moveBg(p.latest.var_pct)}`}>
            {pct(p.latest.var_pct)}
          </span>
          <span className={`text-xs rounded px-2 py-1 ${tendenciaClass(p.latest.tendencia)}`}>
            {p.latest.tendencia ?? "—"}
          </span>
        </div>
      </div>

      <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
        <div className="text-sm font-semibold mb-2">
          Precio S//kg · {p.series.length} días
          <span className="font-normal text-slate-400"> · barras grises = ingreso (t)</span>
        </div>
        <PriceChart series={p.series} />
      </div>

      {st && (
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Mínimo" value={soles(st.min)} />
          <Stat label="Promedio" value={soles(st.avg)} />
          <Stat label="Máximo" value={soles(st.max)} />
          <Stat label="Ingreso hoy" value={tons(p.latest.masa_hoy)} />
        </div>
      )}
    </div>
  );
}
