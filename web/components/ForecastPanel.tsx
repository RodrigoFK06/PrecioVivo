import type { Producto } from "@/lib/data";
import { forecastDe } from "@/lib/data";
import { soles } from "@/lib/format";

/**
 * Pronóstico BETA a 1 día para un producto (server component).
 *
 * Honestidad: el forecast se construye sobre baselines de PRECIO, no de volumen.
 * El kill-gate del snapshot mostró que el volumen NO mejora la predicción, así que
 * aquí no se usa el volumen como predictor. Si no hay pronóstico o hay poca historia
 * (n_obs < 90), no inventamos nada: lo decimos.
 */
export default function ForecastPanel({ producto }: { producto: Producto }) {
  const f = forecastDe(producto);

  // Sin pronóstico, o con poca historia para que sea creíble.
  if (!f || f.n_obs < 90) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <Header />
        <p className="mt-2 text-sm text-slate-500">
          Sin pronóstico {f ? `(poca historia: ${f.n_obs} observaciones)` : "(poca historia)"}.
          Mostramos pronóstico solo cuando hay suficiente serie para que el modelo sea evaluable.
        </p>
      </div>
    );
  }

  const [lo, hi] = f.intervalo;
  const mejorQueBaseline = f.mae_modelo < f.mae_baseline;
  const mejoraPct =
    f.mae_baseline > 0 ? ((f.mae_baseline - f.mae_modelo) / f.mae_baseline) * 100 : null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <Header />

      <div className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-1">
        <span className="text-2xl font-bold tabular-nums">{soles(f.precio_estimado)}</span>
        <span className="text-sm text-slate-400">/kg estimado para mañana</span>
      </div>

      <div className="mt-1 text-sm text-slate-500 tabular-nums">
        Rango probable: {soles(lo)} – {soles(hi)} por kg
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-slate-400">Método</dt>
          <dd className="font-medium">{metodoLegible(f.metodo)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-slate-400">Historia usada</dt>
          <dd className="font-medium tabular-nums">{f.n_obs} días</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-slate-400">Error del modelo (MAE)</dt>
          <dd className="font-medium tabular-nums">{soles(f.mae_modelo)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-slate-400">Baseline ingenuo (MAE)</dt>
          <dd className="font-medium tabular-nums">{soles(f.mae_baseline)}</dd>
        </div>
      </dl>

      <div
        className={`mt-3 rounded-lg px-3 py-2 text-sm ${
          mejorQueBaseline ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-slate-600"
        }`}
      >
        {mejorQueBaseline ? (
          <>
            Mejor que el baseline ingenuo
            {mejoraPct != null && (
              <span className="font-medium tabular-nums"> (−{mejoraPct.toFixed(0)}% de error)</span>
            )}
            . El error menor es mejor.
          </>
        ) : (
          <>Igual o peor que el baseline ingenuo. Tomar el pronóstico con cautela.</>
        )}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
        BETA. Basado en baselines de precio, no en volumen: el volumen es señal de oferta/anomalía,
        no predictor (ver nota metodológica). Referencial, no asesoría de compra.
      </p>
    </div>
  );
}

function Header() {
  return (
    <div className="flex items-center gap-2">
      <h3 className="text-sm font-semibold">Pronóstico a 1 día</h3>
      <span className="text-[10px] uppercase tracking-wide rounded bg-amber-50 text-amber-700 px-1.5 py-0.5 font-medium">
        beta
      </span>
    </div>
  );
}

function metodoLegible(m: string): string {
  switch (m) {
    case "media_tendencia":
      return "Media + tendencia";
    case "ar1":
      return "Autorregresivo (AR1)";
    default:
      return m;
  }
}
