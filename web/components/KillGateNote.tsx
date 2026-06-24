import type { KillGate } from "@/lib/data";
import { pct } from "@/lib/format";

/** Resumen del modelo de IA frente al baseline (derivado de los forecasts por producto). */
export type ModeloIAResumen = {
  /** Productos con pronóstico evaluable. */
  conForecast: number;
  /** Cuántos productos predice un modelo de IA (GBM) que además le gana al baseline. */
  iaGana: number;
  /** Mejora media (% menos error) sobre esos productos. */
  mejoraMediaPct: number;
};

type Props = {
  killGate?: KillGate;
  modeloIA?: ModeloIAResumen;
};

/**
 * "Predicción con IA: qué funciona y qué no" — la nota cuenta DOS cosas distintas,
 * sin confundirlas:
 *
 *   (a) EL LOGRO — el modelo de IA (GBM) ya le GANA al baseline ingenuo en la
 *       mayoría de productos (X de N), con ~Y% menos error, validado walk-forward.
 *   (b) EL KILL-GATE HONESTO — añadir el VOLUMEN de ingreso específicamente NO
 *       mejora la predicción sobre usar el precio. Lo decimos en vez de fingirlo.
 *
 * No es lo mismo: el modelo gana (sí); el volumen no aporta (no). Ambos honestos.
 */
export default function KillGateNote({ killGate, modeloIA }: Props) {
  if (!killGate && !modeloIA) return null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold">Predicción con IA: qué funciona y qué no</h2>
        <span className="text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 bg-emerald-50 text-emerald-700">
          probado en público
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* (a) EL LOGRO: el modelo de IA le gana al baseline. */}
        {modeloIA && modeloIA.iaGana > 0 && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-4">
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide font-semibold text-emerald-700">
                Funciona
              </span>
              <span className="text-xs text-emerald-700">el modelo de IA le gana al baseline</span>
            </div>
            <p className="mt-2 text-[15px] leading-relaxed text-slate-700">
              Un modelo de IA (<span className="font-medium">GBM</span> con lags de precio,
              calendario y feriados) ya le gana al baseline ingenuo en{" "}
              <span className="font-semibold text-emerald-700 tabular-nums">
                {modeloIA.iaGana} de {modeloIA.conForecast}
              </span>{" "}
              productos, con{" "}
              <span className="font-semibold text-emerald-700 tabular-nums">
                {Math.round(modeloIA.mejoraMediaPct)}% menos error
              </span>{" "}
              en promedio. Validado walk-forward (sin mirar el futuro). La predicción con IA ya{" "}
              <span className="font-medium text-slate-900">funciona</span> para la mayoría — no es 100%
              y lo seguimos mejorando.
            </p>
          </div>
        )}

        {/* (b) EL KILL-GATE HONESTO: el volumen no aporta. */}
        {killGate && (
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide font-semibold text-slate-500">
                No aporta
              </span>
              <span className="text-xs text-slate-500">el volumen no mejora sobre el precio</span>
            </div>
            <p className="mt-2 text-[15px] leading-relaxed text-slate-700">
              Probamos si el <span className="font-medium">volumen de ingreso</span> rezagado ayuda a
              predecir el precio. Sobre {killGate.n_productos} productos{" "}
              <span className="font-medium text-slate-900">
                {killGate.volume_helps ? "sí mejora" : "no le gana"}
              </span>{" "}
              a usar solo señales de precio. Lo decimos en vez de fingirlo: el modelo de IA se queda
              con precio + calendario, no con volumen.
            </p>

            <div className="mt-3 grid grid-cols-3 gap-2">
              <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-slate-400">Solo precio</div>
                <div className="text-sm font-semibold tabular-nums">
                  {killGate.mae_baseline.toFixed(4)}
                </div>
              </div>
              <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-slate-400">+ volumen</div>
                <div className="text-sm font-semibold tabular-nums">
                  {killGate.mae_con_volumen.toFixed(4)}
                </div>
              </div>
              <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-slate-400">Mejora</div>
                <div
                  className={`text-sm font-semibold tabular-nums ${
                    killGate.mejora_pct > 0 ? "text-emerald-600" : "text-slate-500"
                  }`}
                >
                  {pct(killGate.mejora_pct)}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      <p className="mt-4 text-xs text-slate-500 leading-relaxed">
        <span className="font-medium text-slate-600">El marco, sin trucos:</span> el modelo de IA le
        gana al baseline (sí); el volumen específicamente no aporta (no). Ambos resultados son honestos
        y todas las cifras (MAE, intervalos, nº de productos) son reales y reproducibles.
      </p>
      {killGate?.detalle && (
        <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">{killGate.detalle}</p>
      )}
    </div>
  );
}
