import type { ComparacionHonesta, KillGate } from "@/lib/data";
import { pct } from "@/lib/format";

/**
 * Resumen DESCRIPTIVO de la capa de pronóstico. No afirma superioridad:
 * `porMetodo` dice qué método quedó elegido en cada producto, que es un hecho
 * sobre la selección, no sobre la capacidad. La afirmación de capacidad vive en
 * `ComparacionHonesta`.
 */
export type ModeloIAResumen = {
  /** Productos con pronóstico evaluable. */
  conForecast: number;
  /** Cuántos productos eligieron cada método (gbm, media_tendencia, …). */
  porMetodo: Record<string, number>;
};

type Props = {
  killGate?: KillGate;
  modeloIA?: ModeloIAResumen;
  comparacion?: ComparacionHonesta | null;
};

const NOMBRE_METODO: Record<string, string> = {
  gbm: "GBM",
  media_tendencia: "media + tendencia",
  seasonal_naive: "seasonal naive",
  naive: "naive",
  sin_datos: "sin datos",
};

/**
 * "Predicción con IA: qué funciona y qué no" — tres cosas distintas, sin
 * confundirlas:
 *
 *   (a) QUÉ MODELO GANA — comparación por familia, cada una evaluada por
 *       separado sobre el mismo conjunto de productos. Es la única cifra sin
 *       sesgo de selección, y hoy dice que una recta AR(1) le gana al GBM.
 *   (b) QUÉ NO APORTA — el volumen de ingreso rezagado no mejora sobre el
 *       precio. Lo decimos en vez de fingirlo.
 *   (c) QUÉ NO SIGNIFICA NADA — que el método elegido por producto "le gane" a
 *       su baseline, porque se eligió justamente por eso.
 *
 * El bloque (a) reemplaza a una afirmación anterior ("el modelo le gana al
 * baseline en X de N productos, ~Y% menos error") que era tautológica: contaba
 * productos donde `metodo === "gbm" && mae_modelo < mae_baseline`, y
 * `forecast_producto` elige `metodo` como el argmin del MAE sobre un conjunto
 * que ya incluye al baseline. Se cumplía en el 100% de los productos.
 */
export default function KillGateNote({ killGate, modeloIA, comparacion }: Props) {
  if (!killGate && !modeloIA && !comparacion) return null;

  return (
    <div className="rounded-sm border border-rule bg-card p-5 sm:p-6">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="font-serif text-lg text-ink">Predicción con IA: qué funciona y qué no</h2>
        <span className="text-[10px] uppercase tracking-wide rounded-sm px-1.5 py-0.5 bg-down/10 text-down">
          probado en público
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* (a) QUÉ MODELO GANA — comparación sin sesgo de selección. */}
        {comparacion && (
          <div className="rounded-sm border border-down/30 bg-down/[0.06] p-4">
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide font-semibold text-down">
                Qué gana
              </span>
              <span className="text-xs text-down">
                {comparacion.gbmGana
                  ? "el modelo complejo es además el mejor"
                  : "el modelo simple le gana al complejo"}
              </span>
            </div>

            <p className="mt-2 text-[15px] leading-relaxed text-ink">
              Cada familia de modelo se evaluó por separado sobre los mismos{" "}
              <span className="tabular-nums">{comparacion.nProductos}</span> productos,
              walk-forward y sin mirar el futuro. Gana{" "}
              <span className="font-semibold text-down">{comparacion.ganador.etiqueta}</span> —{" "}
              {comparacion.ganador.descripcion} — con{" "}
              <span className="font-semibold text-down tabular-nums">
                {Math.round(comparacion.ganador.mejoraVsBaselinePct)}% menos error
              </span>{" "}
              que el baseline.
              {!comparacion.gbmGana && comparacion.gbm && (
                <>
                  {" "}
                  El <span className="font-medium">GBM</span>, que es el modelo más complejo que
                  entrenamos, queda por detrás (
                  <span className="tabular-nums">{comparacion.gbm.mae.toFixed(4)}</span> contra{" "}
                  <span className="tabular-nums">{comparacion.ganador.mae.toFixed(4)}</span>). Con
                  esta cantidad de historia, la complejidad todavía no se paga.
                </>
              )}
            </p>

            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide text-faint">
                  <th className="text-left font-normal pb-1">Familia</th>
                  <th className="text-right font-normal pb-1">MAE</th>
                  <th className="text-right font-normal pb-1">vs baseline</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {comparacion.familias.map((f) => (
                  <tr key={f.clave} className={f.clave === comparacion.ganador.clave ? "text-ink" : "text-muted"}>
                    <td className="py-1">
                      {f.clave === comparacion.ganador.clave ? (
                        <span className="font-semibold">{f.etiqueta}</span>
                      ) : (
                        f.etiqueta
                      )}
                    </td>
                    <td className="py-1 text-right tabular-nums">{f.mae.toFixed(4)}</td>
                    <td
                      className={`py-1 text-right tabular-nums ${
                        f.mejoraVsBaselinePct > 0 ? "text-down" : "text-faint"
                      }`}
                    >
                      {f.clave === "baseline" ? "—" : pct(f.mejoraVsBaselinePct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-faint leading-relaxed">
              MAE en S/ por kg, horizonte {comparacion.horizonte} día hábil. Menos es mejor.
            </p>
          </div>
        )}

        {/* (b) EL KILL-GATE HONESTO: el volumen no aporta. */}
        {killGate && (
          <div className="rounded-sm border border-rule bg-card p-4">
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide font-semibold text-muted">
                No aporta
              </span>
              <span className="text-xs text-muted">el volumen no mejora sobre el precio</span>
            </div>
            <p className="mt-2 text-[15px] leading-relaxed text-ink">
              Probamos si el <span className="font-medium">volumen de ingreso</span> rezagado ayuda a
              predecir el precio. Sobre {killGate.n_productos} productos{" "}
              <span className="font-medium text-ink">
                {killGate.volume_helps ? "sí mejora" : "no le gana"}
              </span>{" "}
              a usar solo señales de precio. Lo decimos en vez de fingirlo: el modelo se queda con
              precio + calendario, no con volumen.
            </p>

            <div className="mt-3 grid grid-cols-3 gap-2">
              <div className="rounded-sm border border-rule bg-card px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-faint">Solo precio</div>
                <div className="text-sm font-semibold tabular-nums">
                  {killGate.mae_baseline.toFixed(4)}
                </div>
              </div>
              <div className="rounded-sm border border-rule bg-card px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-faint">+ volumen</div>
                <div className="text-sm font-semibold tabular-nums">
                  {killGate.mae_con_volumen.toFixed(4)}
                </div>
              </div>
              <div className="rounded-sm border border-rule bg-card px-2.5 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-faint">Mejora</div>
                <div
                  className={`text-sm font-semibold tabular-nums ${
                    killGate.mejora_pct > 0 ? "text-down" : "text-muted"
                  }`}
                >
                  {pct(killGate.mejora_pct)}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* (c) Qué método quedó elegido — descriptivo, con su advertencia. */}
      {modeloIA && Object.keys(modeloIA.porMetodo).length > 0 && (
        <p className="mt-4 text-xs text-muted leading-relaxed">
          <span className="font-medium text-ink">Qué método usa cada producto:</span>{" "}
          {Object.entries(modeloIA.porMetodo)
            .sort((a, b) => b[1] - a[1])
            .map(([m, n]) => `${NOMBRE_METODO[m] ?? m} en ${n}`)
            .join(", ")}{" "}
          de {modeloIA.conForecast}. Se elige por producto, quedándose con el de menor error
          walk-forward.{" "}
          <span className="text-faint">
            Ojo con leer esto como una victoria: el método se elige comparándolo contra el baseline,
            así que el elegido siempre le gana. Por eso la cifra que publicamos arriba es la
            comparación por familia, donde ninguna participó en seleccionar a las otras.
          </span>
        </p>
      )}

      {killGate?.detalle && (
        <p className="mt-2 text-[11px] text-faint leading-relaxed">{killGate.detalle}</p>
      )}
    </div>
  );
}
