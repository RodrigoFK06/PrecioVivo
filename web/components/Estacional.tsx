import type { Producto } from "@/lib/data";
import { estacionalDelProducto, promedioMensualHistorico } from "@/lib/data";
import { soles, pct } from "@/lib/format";

type Props = { producto: Producto };

// MESES_LARGO no se exporta desde lib/format; lo replicamos aquí (constante pura,
// server/cliente-safe) para nombrar el mes vigente sin construir Date (evita TZ).
const MESES_LARGO = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];
const MESES_CORTO = ["E", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

/**
 * Estacionalidad de un producto: compara su precio actual contra el promedio
 * histórico del MISMO mes calendario (todos los años de su serie). Server-safe.
 *
 * Honesto: si no hay precio actual, ni promedio del mes, o solo existe el año en
 * curso (sin años previos para comparar), lo dice en vez de inventar una cifra.
 */
export default function Estacional({ producto }: Props) {
  const est = estacionalDelProducto(producto);

  // Promedio histórico por cada mes 1..12 (null si ese mes no tiene datos).
  const porMes = Array.from({ length: 12 }, (_, k) =>
    promedioMensualHistorico(producto, k + 1),
  );
  const conDato = porMes.filter((v): v is number => v != null);

  if (!est || conDato.length === 0) {
    return (
      <div className="rounded-sm border border-rule bg-card p-5">
        <h2 className="font-serif text-base text-ink mb-2">Estacionalidad</h2>
        <p className="text-muted text-sm leading-relaxed">
          Todavía no hay suficiente historia de {producto.nombre} para comparar su
          precio de hoy con su mismo mes en años anteriores.
        </p>
      </div>
    );
  }

  const { mes, promedio, actual, desviacionPct } = est;
  const mesNombre = MESES_LARGO[mes - 1];

  // ¿Tenemos años previos para ese mes, o solo el año en curso? Si la serie de
  // ese mes cae toda dentro de un único año, la comparación es contra sí mismo.
  const aniosDelMes = new Set(
    producto.series
      .filter((p) => p.precio_kg != null && +p.fecha.slice(5, 7) === mes)
      .map((p) => p.fecha.slice(0, 4)),
  );
  const soloUnAnio = aniosDelMes.size < 2;

  const masCaro = desviacionPct > 0;
  const direccion = masCaro ? "por encima" : "por debajo";
  // Más caro = up (rojo, malo para el bolsillo); más barato = down (verde).
  const tono = masCaro ? "text-up" : "text-down";
  const chipTono = masCaro
    ? "bg-up/10 text-up"
    : "bg-down/10 text-down";

  // --- Mini gráfico de barras (promedio histórico por mes) ---
  const W = 320;
  const H = 96;
  const padT = 8;
  const padB = 18;
  const chartH = H - padT - padB;
  const maxVal = Math.max(...conDato);
  const minVal = Math.min(...conDato);
  // Escala con base flotante para que las diferencias entre meses se vean.
  const base = minVal - (maxVal - minVal) * 0.25;
  const span = maxVal - base || 1;
  const slot = W / 12;
  const barW = slot * 0.62;

  return (
    <div className="rounded-sm border border-rule bg-card p-5 sm:p-6">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="font-serif text-base text-ink">Estacionalidad</h2>
        {soloUnAnio && (
          <span
            className="text-[10px] uppercase tracking-wide rounded-sm bg-ink/[0.05] text-muted px-1.5 py-0.5"
            title="Aún no hay un año anterior completo de este mes; la referencia usa solo el año en curso."
          >
            historia limitada
          </span>
        )}
      </div>

      <p className="text-ink leading-relaxed text-[15px]">
        <span className="font-medium text-ink">{producto.nombre}</span> está{" "}
        <span className={`font-semibold tabular-nums ${tono}`}>
          {pct(desviacionPct)}
        </span>{" "}
        {direccion} de su {mesNombre} histórico{" "}
        <span className="text-muted tabular-nums">
          ({soles(actual)} vs {soles(promedio)})
        </span>
        .
      </p>
      {soloUnAnio && (
        <p className="mt-1.5 text-xs text-muted leading-relaxed">
          Nota: por ahora el promedio de {mesNombre} se calcula solo con el año en
          curso. La comparación entre años gana sentido cuando acumulemos otro{" "}
          {mesNombre}.
        </p>
      )}

      <div className="mt-4">
        <div className="text-[11px] uppercase tracking-wide text-faint mb-1">
          Promedio histórico por mes (S/ por kg)
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label={`Promedio histórico por mes de ${producto.nombre}; mes vigente: ${mesNombre}`}
        >
          {porMes.map((v, i) => {
            const cx = i * slot + slot / 2;
            const vigente = i + 1 === mes;
            if (v == null) {
              // Mes sin datos: marca tenue en la base.
              return (
                <g key={i}>
                  <line
                    x1={cx - barW / 2}
                    y1={padT + chartH}
                    x2={cx + barW / 2}
                    y2={padT + chartH}
                    stroke="#e2dccd"
                    strokeWidth={2}
                  />
                  <text
                    x={cx}
                    y={H - 5}
                    textAnchor="middle"
                    className="fill-faint"
                    fontSize="9"
                  >
                    {MESES_CORTO[i]}
                  </text>
                </g>
              );
            }
            const h = Math.max(2, ((v - base) / span) * chartH);
            const y = padT + chartH - h;
            const fill = vigente ? "#15402a" : "#cbc4b1";
            return (
              <g key={i}>
                <rect
                  x={cx - barW / 2}
                  y={y}
                  width={barW}
                  height={h}
                  rx={1.5}
                  fill={fill}
                >
                  <title>
                    {MESES_LARGO[i]}: {soles(v)}
                    {vigente ? " · mes vigente" : ""}
                  </title>
                </rect>
                <text
                  x={cx}
                  y={H - 5}
                  textAnchor="middle"
                  className={vigente ? "fill-accent font-semibold" : "fill-faint"}
                  fontSize="9"
                >
                  {MESES_CORTO[i]}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="mt-1 flex items-center gap-3 text-[11px] text-faint">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-accent" />
            {mesNombre} (vigente)
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-rule-strong" />
            otros meses
          </span>
        </div>
      </div>
    </div>
  );
}
