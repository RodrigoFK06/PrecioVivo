import type { KillGate } from "@/lib/data";
import { pct } from "@/lib/format";

type Props = {
  killGate?: KillGate;
};

/**
 * Nota metodológica honesta. Antes de afirmar "el volumen predice el precio",
 * lo probamos contra un control que solo usa el precio rezagado (AR(1)).
 * Si volume_helps=false, lo decimos con orgullo de rigor: no inflamos la tesis.
 */
export default function KillGateNote({ killGate }: Props) {
  if (!killGate) return null;

  const { volume_helps, mae_baseline, mae_con_volumen, mejora_pct, detalle, n_productos } = killGate;

  // Mejor MAE = menor MAE. El "ganador" va en emerald.
  const baselineGana = mae_baseline <= mae_con_volumen;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-sm font-semibold">Nota metodológica · ¿el volumen predice el precio?</h2>
        <span
          className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 ${
            volume_helps ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"
          }`}
        >
          {volume_helps ? "el volumen ayuda" : "el volumen no ayuda"}
        </span>
      </div>

      <p className="text-slate-700 leading-relaxed text-[15px]">
        {volume_helps ? (
          <>
            Pusimos a prueba si añadir el volumen rezagado mejora la predicción del precio frente a un
            control que solo usa el precio del día anterior (AR(1)). Sobre {n_productos} productos, sí
            mejora el error de predicción.
          </>
        ) : (
          <>
            Pusimos a prueba si añadir el volumen rezagado mejora la predicción del precio frente a un
            control que solo usa el precio del día anterior (AR(1)). Sobre {n_productos} productos,{" "}
            <span className="font-medium text-slate-900">no mejora</span>: por eso tratamos el volumen
            como señal de oferta y de anomalía, no como predictor del precio. El pronóstico es BETA y se
            apoya en baselines de precio.
          </>
        )}
      </p>

      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div
          className={`rounded-lg border px-3 py-2 ${
            baselineGana ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-white"
          }`}
        >
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            MAE solo precio (AR1)
          </div>
          <div className="text-base font-semibold tabular-nums">{mae_baseline.toFixed(4)}</div>
        </div>
        <div
          className={`rounded-lg border px-3 py-2 ${
            !baselineGana ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-white"
          }`}
        >
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            MAE precio + volumen
          </div>
          <div className="text-base font-semibold tabular-nums">{mae_con_volumen.toFixed(4)}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            Mejora con volumen
          </div>
          <div
            className={`text-base font-semibold tabular-nums ${
              mejora_pct > 0 ? "text-emerald-600" : "text-slate-500"
            }`}
          >
            {pct(mejora_pct)}
          </div>
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-400 leading-relaxed">{detalle}</p>
    </div>
  );
}
