import { type MercadoExtra } from "@/lib/data";
import { soles, pct, fechaCorta, moveBg } from "@/lib/format";

type Props = {
  mercados: MercadoExtra[];
};

/**
 * Otros mercados además del GMML: pollo vivo del Mercado de Aves (vía SISAP)
 * y frutas del MMF2 (vía boletín). Cada mercado muestra su último día
 * disponible; estos productos no tienen página propia (aún no son serie
 * curada), así que son tarjetas informativas, no links.
 */
export default function MercadosExtra({ mercados }: Props) {
  if (mercados.length === 0) return null;
  return (
    <div className="grid gap-5 md:grid-cols-2">
      {mercados.map((m) => (
        <div key={m.codigo} className="rounded-md border border-rule bg-card p-5">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-base font-semibold tracking-tight text-ink">{m.nombre}</h3>
            <span className="shrink-0 text-xs text-faint">
              datos al {fechaCorta(m.latestFecha)}
            </span>
          </div>
          <ul className="mt-3 divide-y divide-rule">
            {m.productos
              .filter((p) => p.precio_kg != null)
              .map((p) => (
                <li key={p.slug} className="flex items-baseline justify-between gap-2 py-2">
                  <span className="truncate text-[15px] text-ink">{p.nombre}</span>
                  <span className="flex items-baseline gap-2 shrink-0 tabular-nums">
                    <span className="text-sm text-muted">{soles(p.precio_kg)}</span>
                    {p.var_pct != null && (
                      <span
                        className={`text-sm font-medium rounded px-1.5 py-0.5 ${moveBg(p.var_pct)}`}
                      >
                        {pct(p.var_pct)}
                      </span>
                    )}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
