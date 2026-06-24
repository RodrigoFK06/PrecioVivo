import type { Producto } from "@/lib/data";
import { forecastDe } from "@/lib/data";
import { soles } from "@/lib/format";

/**
 * Pronóstico a 1 día para un producto (server component).
 *
 * Honestidad matizada (DOS cosas distintas, no se confunden):
 *  (1) El MODELO de IA (GBM con lags + calendario + feriados) ya le GANA al
 *      baseline ingenuo en la mayoría de productos, validado walk-forward. Para
 *      esos productos (metodo == 'gbm' y mae_modelo < mae_baseline) lo mostramos
 *      como "Pronóstico con IA" con su MAE real y el % de mejora.
 *  (2) El VOLUMEN específicamente no aporta sobre el precio (kill-gate): por eso
 *      el modelo se construye sobre señales de precio + calendario, no de volumen.
 *
 * Si no hay pronóstico, hay poca historia (n_obs < 90), o el modelo no le gana al
 * baseline, no inventamos nada: lo decimos.
 */
export default function ForecastPanel({ producto }: { producto: Producto }) {
  const f = forecastDe(producto);

  // Sin pronóstico, o con poca historia para que sea creíble.
  if (!f || f.n_obs < 90) {
    return (
      <div className="rounded-sm border border-rule bg-card p-4">
        <Header esIA={false} beta />
        <p className="mt-2 text-sm text-muted">
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
  // Modelo de IA que de verdad le gana al baseline: lo tratamos como tal, no como beta.
  const esIAGanadora = f.metodo === "gbm" && mejorQueBaseline;

  return (
    <div className="rounded-sm border border-rule bg-card p-4">
      <Header esIA={esIAGanadora} beta={!esIAGanadora} />

      <div className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-1">
        <span className="text-2xl font-bold tabular-nums">{soles(f.precio_estimado)}</span>
        <span className="text-sm text-faint">/kg estimado para mañana</span>
      </div>

      <div className="mt-1 text-sm text-muted tabular-nums">
        Rango probable: {soles(lo)} – {soles(hi)} por kg
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-faint">Método</dt>
          <dd className="font-medium">{metodoLegible(f.metodo)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-faint">Historia usada</dt>
          <dd className="font-medium tabular-nums">{f.n_obs} días</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-faint">Error del modelo (MAE)</dt>
          <dd className="font-medium tabular-nums">{soles(f.mae_modelo)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-faint">Baseline ingenuo (MAE)</dt>
          <dd className="font-medium tabular-nums">{soles(f.mae_baseline)}</dd>
        </div>
      </dl>

      <div
        className={`mt-3 rounded-sm px-3 py-2 text-sm ${
          mejorQueBaseline ? "bg-down/10 text-down" : "bg-ink/[0.05] text-muted"
        }`}
      >
        {esIAGanadora ? (
          <>
            El modelo de IA le gana al baseline ingenuo
            {mejoraPct != null && (
              <span className="font-medium tabular-nums"> ({mejoraPct.toFixed(0)}% menos error)</span>
            )}
            , validado walk-forward (sin mirar el futuro). El error menor es mejor.
          </>
        ) : mejorQueBaseline ? (
          <>
            Mejor que el baseline ingenuo
            {mejoraPct != null && (
              <span className="font-medium tabular-nums"> ({mejoraPct.toFixed(0)}% menos error)</span>
            )}
            . El error menor es mejor.
          </>
        ) : (
          <>Igual o peor que el baseline ingenuo aquí. Tomar el pronóstico con cautela.</>
        )}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-faint">
        {esIAGanadora ? (
          <>
            Modelo GBM (árboles con lags de precio, calendario y feriados), evaluado walk-forward
            anti-leakage. El volumen de ingreso se probó y no mejora la predicción del precio (ver
            nota metodológica), por eso no entra como predictor. Referencial, no asesoría de compra.
          </>
        ) : (
          <>
            En camino a la predicción con IA. Para este producto el modelo aún no supera de forma
            clara al baseline de precio; el volumen tampoco mejora la predicción (ver nota
            metodológica). Referencial, no asesoría de compra.
          </>
        )}
      </p>
    </div>
  );
}

function Header({ esIA, beta }: { esIA: boolean; beta: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="font-serif text-base text-ink">
        {esIA ? "Pronóstico con IA · 1 día" : "Pronóstico a 1 día"}
      </h3>
      {esIA && (
        <span className="text-[10px] uppercase tracking-wide rounded-sm bg-down/10 text-down px-1.5 py-0.5 font-medium">
          modelo GBM · le gana al baseline
        </span>
      )}
      {beta && (
        <span className="text-[10px] uppercase tracking-wide rounded-sm bg-ink/[0.05] text-muted px-1.5 py-0.5 font-medium">
          beta
        </span>
      )}
    </div>
  );
}

function metodoLegible(m: string): string {
  switch (m) {
    case "gbm":
      return "IA · Gradient Boosting (GBM)";
    case "seasonal_naive":
      return "Estacional ingenuo";
    case "media_tendencia":
      return "Media + tendencia";
    case "ar1":
      return "Autorregresivo (AR1)";
    default:
      return m;
  }
}
