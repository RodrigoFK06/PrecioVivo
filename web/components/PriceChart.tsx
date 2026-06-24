import type { Point } from "@/lib/data";
import { fechaCorta } from "@/lib/format";

type Props = { series: Point[] };

// Larger price line chart with area fill + volume bars underneath. Pure SVG.
export default function PriceChart({ series }: Props) {
  const W = 760;
  const H = 280;
  const padL = 44;
  const padR = 14;
  const padT = 16;
  const priceH = 180;
  const volTop = padT + priceH + 18;
  const volH = 48;

  const pts = series
    .map((p, i) => ({ ...p, i }))
    .filter((p): p is Point & { i: number; precio_kg: number } => p.precio_kg != null);
  if (pts.length < 2) return <div className="text-faint text-sm">Sin datos suficientes.</div>;

  const n = series.length - 1;
  const prices = pts.map((p) => p.precio_kg);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const pad = (max - min) * 0.12 || 0.5;
  const lo = Math.max(0, min - pad);
  const hi = max + pad;

  const x = (i: number) => padL + (i / n) * (W - padL - padR);
  const yP = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * priceH;

  const line = pts.map((p, k) => `${k === 0 ? "M" : "L"}${x(p.i).toFixed(1)},${yP(p.precio_kg).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts[pts.length - 1].i).toFixed(1)},${(padT + priceH).toFixed(1)} L${x(pts[0].i).toFixed(1)},${(padT + priceH).toFixed(1)} Z`;

  const vols = series.map((p) => p.masa_hoy);
  const maxVol = Math.max(...vols.filter((v): v is number => v != null), 1);

  const gridVals = [hi, (hi + lo) / 2, lo];
  const last = pts[pts.length - 1];

  // ~6 date ticks across the series
  const ticks = Array.from({ length: 6 }, (_, k) => Math.round((k / 5) * n));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Serie de precio">
      <defs>
        <linearGradient id="pv-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#15402a" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#15402a" stopOpacity="0" />
        </linearGradient>
      </defs>

      {gridVals.map((v, k) => (
        <g key={k}>
          <line x1={padL} y1={yP(v)} x2={W - padR} y2={yP(v)} stroke="#e2dccd" strokeWidth={1} />
          <text x={padL - 6} y={yP(v) + 3} textAnchor="end" className="fill-faint" fontSize="10">
            {v.toFixed(2)}
          </text>
        </g>
      ))}

      <path d={area} fill="url(#pv-area)" />
      <path d={line} fill="none" stroke="#15402a" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.i)} cy={yP(last.precio_kg)} r={3.5} fill="#15402a" />

      {/* volume bars */}
      {series.map((p, i) =>
        p.masa_hoy != null ? (
          <rect
            key={i}
            x={x(i) - 1.4}
            y={volTop + volH - (p.masa_hoy / maxVol) * volH}
            width={2.8}
            height={(p.masa_hoy / maxVol) * volH}
            fill="#cbc4b1"
            rx={0.5}
          />
        ) : null,
      )}
      <text x={padL - 6} y={volTop + 8} textAnchor="end" className="fill-faint" fontSize="9">
        t
      </text>

      {ticks.map((ti, k) => (
        <text key={k} x={x(ti)} y={H - 2} textAnchor="middle" className="fill-faint" fontSize="10">
          {series[ti] ? fechaCorta(series[ti].fecha) : ""}
        </text>
      ))}
    </svg>
  );
}
