import type { KillGate } from "@/lib/data";
import { pct } from "@/lib/format";

type Props = {
  killGate?: KillGate;
};

/**
 * Hoja de ruta honesta hacia la predicción de precios con IA.
 *
 * La META es predecir los precios con IA. Lo construimos en público y con rigor:
 * cada hipótesis se prueba contra un baseline y solo se conserva si de verdad
 * mejora. Aquí mostramos el primer experimento (¿el volumen ayuda?) con su
 * resultado real — sin inflarlo y sin abandonar la meta.
 */
export default function KillGateNote({ killGate }: Props) {
  if (!killGate) return null;

  const { volume_helps, mae_baseline, mae_con_volumen, mejora_pct, detalle, n_productos } = killGate;
  const baselineGana = mae_baseline <= mae_con_volumen;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-sm font-semibold">Hacia la predicción de precios con IA</h2>
        <span className="text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 bg-emerald-50 text-emerald-700">
          en construcción · en público
        </span>
      </div>

      <p className="text-slate-700 leading-relaxed text-[15px]">
        Nuestra meta es <span className="font-medium text-slate-900">predecir los precios mayoristas con IA</span>.
        Lo construimos con rigor: probamos cada hipótesis contra un baseline y nos quedamos solo con lo que
        de verdad mejora la predicción.{" "}
        {volume_helps ? (
          <>
            Primer hallazgo: añadir el <span className="font-medium">volumen de ingreso</span> rezagado{" "}
            <span className="font-medium text-emerald-700">sí mejora</span> la predicción del precio sobre{" "}
            {n_productos} productos. Vamos por ese camino.
          </>
        ) : (
          <>
            Primer experimento — ¿el <span className="font-medium">volumen de ingreso</span> ayuda a predecir
            el precio? Con la data de hoy ({n_productos} productos, ~6 meses){" "}
            <span className="font-medium text-slate-900">todavía no le gana</span> a usar solo el precio
            anterior. Lo decimos en vez de fingirlo. Por eso el pronóstico de hoy es{" "}
            <span className="font-medium">beta</span> y parte de baselines de precio mientras construimos el
            modelo.
          </>
        )}
      </p>

      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div className={`rounded-lg border px-3 py-2 ${baselineGana ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-white"}`}>
          <div className="text-[11px] uppercase tracking-wide text-slate-400">MAE solo precio (AR1)</div>
          <div className="text-base font-semibold tabular-nums">{mae_baseline.toFixed(4)}</div>
        </div>
        <div className={`rounded-lg border px-3 py-2 ${!baselineGana ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-white"}`}>
          <div className="text-[11px] uppercase tracking-wide text-slate-400">MAE precio + volumen</div>
          <div className="text-base font-semibold tabular-nums">{mae_con_volumen.toFixed(4)}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">Mejora con volumen</div>
          <div className={`text-base font-semibold tabular-nums ${mejora_pct > 0 ? "text-emerald-600" : "text-slate-500"}`}>
            {pct(mejora_pct)}
          </div>
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-500 leading-relaxed">
        <span className="font-medium text-slate-600">Siguiente paso:</span> más historia (ya hay 2+ años
        disponibles) y más señales (estacionalidad, calendario, clima) para que un modelo de IA supere al
        baseline. Lo iremos publicando.
      </p>
      <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">{detalle}</p>
    </div>
  );
}
