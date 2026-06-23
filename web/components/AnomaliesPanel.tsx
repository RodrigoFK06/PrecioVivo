import Link from "next/link";
import type { Anomalia } from "@/lib/data";

type Props = {
  anomalias?: Anomalia[];
};

/**
 * Para una anomalía de precio, z>0 = precio anormalmente ALTO (rose, malo para
 * el comprador) y z<0 = anormalmente BAJO (emerald). Para volumen, el signo
 * indica ingreso por encima/por debajo de lo habitual (señal de oferta, neutra).
 */
function colorPorDireccion(a: Anomalia): string {
  if (a.tipo === "volumen") return "bg-sky-50 text-sky-700";
  return a.z > 0 ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700";
}

function etiquetaTipo(tipo: Anomalia["tipo"]): string {
  return tipo === "precio" ? "Precio" : "Volumen";
}

export default function AnomaliesPanel({ anomalias }: Props) {
  const items = anomalias ?? [];
  if (items.length === 0) return null;

  return (
    <div className="h-full rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm">
      <div className="px-4 py-3 border-b border-slate-200">
        <h2 className="text-sm font-semibold">Anomalías del día</h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Valores que se alejan mucho de lo normal (|z| alto). Precio o volumen.
        </p>
      </div>
      <ul className="divide-y divide-slate-50">
        {items.map((a, i) => (
          <li key={`${a.slug}-${a.tipo}-${i}`}>
            <Link
              href={`/p/${a.slug}`}
              className="group flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-slate-50 transition-colors"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-slate-700 group-hover:text-slate-900">{a.nombre}</span>
                <span className="block truncate text-xs text-slate-500">{a.detalle}</span>
              </span>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] uppercase tracking-wide rounded bg-slate-100 text-slate-500 px-1.5 py-0.5">
                  {etiquetaTipo(a.tipo)}
                </span>
                <span
                  className={`text-xs font-medium tabular-nums rounded px-1.5 py-0.5 ${colorPorDireccion(a)}`}
                  title="z-score: desviaciones respecto a lo normal"
                >
                  {a.z > 0 ? "+" : ""}
                  {a.z.toFixed(1)}σ
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
