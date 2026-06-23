import Link from "next/link";
import { getSnapshot, movers, supplySurges, type Producto } from "@/lib/data";
import { soles, pct, tons, fechaLarga, moveBg, tendenciaClass } from "@/lib/format";
import Sparkline from "@/components/Sparkline";

export const dynamic = "force-static";

function resumenDelDia(
  fecha: string,
  total: number,
  up: Producto[],
  down: Producto[],
  surge: Producto[],
): string {
  const nUp = up.filter((p) => (p.latest.var_pct ?? 0) > 0).length;
  const nDown = down.filter((p) => (p.latest.var_pct ?? 0) < 0).length;
  const parts: string[] = [];
  parts.push(
    `De ${total} productos del mercado, ${nUp} subieron y ${nDown} bajaron de precio respecto a ayer.`,
  );
  if (up[0]?.latest.var_pct)
    parts.push(`Lo que más se encareció: ${up[0].nombre} (${pct(up[0].latest.var_pct)}).`);
  if (down[0]?.latest.var_pct)
    parts.push(`Lo que más bajó: ${down[0].nombre} (${pct(down[0].latest.var_pct)}).`);
  const s = surge[0];
  if (s && s.latest.masa_hoy && s.latest.masa_7d)
    parts.push(
      `Fuerte ingreso de ${s.nombre.toLowerCase()}: ${tons(s.latest.masa_hoy)} hoy vs ${tons(
        Math.round(s.latest.masa_7d),
      )} de promedio.`,
    );
  return parts.join(" ");
}

function MoverList({ items, dir }: { items: Producto[]; dir: "up" | "down" }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
        <span className={dir === "up" ? "text-rose-600" : "text-emerald-600"}>
          {dir === "up" ? "▲ Más caros hoy" : "▼ Más baratos hoy"}
        </span>
      </h3>
      <ul className="space-y-1.5">
        {items.map((p) => (
          <li key={p.slug}>
            <Link
              href={`/p/${p.slug}`}
              className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50"
            >
              <span className="truncate text-sm">{p.nombre}</span>
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
  const surge = supplySurges(snap, 3);
  const productos = [...snap.productos].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      {/* hero */}
      <div className="mb-6">
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Precios mayoristas, en vivo</h1>
        <p className="text-slate-500 mt-1">
          {snap.mercado} · actualizado el{" "}
          <span className="font-medium text-slate-700">{fechaLarga(snap.latestFecha)}</span>
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-white border border-slate-200 px-3 py-1">
            {snap.productCount} productos
          </span>
          <span className="rounded-full bg-white border border-slate-200 px-3 py-1">
            {snap.fechas.length} días de historia
          </span>
          <span className="rounded-full bg-emerald-50 text-emerald-700 px-3 py-1 font-medium">
            precios en S/ por kg
          </span>
        </div>
      </div>

      {/* daily summary */}
      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex items-center gap-2 mb-2">
          <h2 className="text-sm font-semibold">Resumen del día</h2>
          <span className="text-[10px] uppercase tracking-wide rounded bg-slate-100 text-slate-500 px-1.5 py-0.5">
            beta · IA próximamente
          </span>
        </div>
        <p className="text-slate-700 leading-relaxed text-[15px]">
          {resumenDelDia(snap.latestFecha, snap.productCount, up, down, surge)}
        </p>
      </div>

      {/* movers */}
      <div className="grid sm:grid-cols-2 gap-4 mb-8">
        <MoverList items={up} dir="up" />
        <MoverList items={down} dir="down" />
      </div>

      {/* full table */}
      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-200">
          <h2 className="text-sm font-semibold">Todos los productos</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                <th className="px-4 py-2 font-medium">Producto</th>
                <th className="px-4 py-2 font-medium text-right">S//kg hoy</th>
                <th className="px-4 py-2 font-medium text-right">Δ ayer</th>
                <th className="px-4 py-2 font-medium">Tendencia</th>
                <th className="px-4 py-2 font-medium text-right">Ingreso hoy</th>
                <th className="px-4 py-2 font-medium">30 días</th>
              </tr>
            </thead>
            <tbody>
              {productos.map((p) => (
                <tr key={p.slug} className="border-b border-slate-50 hover:bg-slate-50/70">
                  <td className="px-4 py-2">
                    <Link href={`/p/${p.slug}`} className="hover:text-emerald-600 hover:underline">
                      {p.nombre}
                    </Link>
                    <span className="text-xs text-slate-400 ml-1.5">/{p.unidad.toLowerCase()}</span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{soles(p.latest.precio_kg)}</td>
                  <td className="px-4 py-2 text-right">
                    <span className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${moveBg(p.latest.var_pct)}`}>
                      {pct(p.latest.var_pct)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-xs rounded px-1.5 py-0.5 ${tendenciaClass(p.latest.tendencia)}`}>
                      {p.latest.tendencia ?? "—"}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-slate-500">
                    {tons(p.latest.masa_hoy)}
                  </td>
                  <td className="px-4 py-2">
                    <Sparkline values={p.series.slice(-30).map((pt) => pt.precio_kg)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
