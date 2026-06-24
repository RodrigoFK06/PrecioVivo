type Props = {
  values: (number | null)[];
  width?: number;
  height?: number;
  className?: string;
};

// Tiny inline trend line. Pure SVG, no deps. Color: pricier=up (deep red), cheaper=down (deep green).
export default function Sparkline({ values, width = 120, height = 34, className = "" }: Props) {
  const pts = values
    .map((v, i) => ({ v, i }))
    .filter((p): p is { v: number; i: number } => p.v != null);

  if (pts.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden />;
  }

  const min = Math.min(...pts.map((p) => p.v));
  const max = Math.max(...pts.map((p) => p.v));
  const n = values.length - 1;
  const x = (i: number) => (i / n) * (width - 2) + 1;
  const y = (v: number) => {
    const r = max === min ? 0.5 : (v - min) / (max - min);
    return height - 2 - r * (height - 4);
  };

  const d = pts
    .map((p, k) => `${k === 0 ? "M" : "L"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`)
    .join(" ");
  const up = pts[pts.length - 1].v >= pts[0].v;
  const stroke = up ? "#a72a18" : "#1c7a47";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden
    >
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(pts[pts.length - 1].i)} cy={y(pts[pts.length - 1].v)} r={1.8} fill={stroke} />
    </svg>
  );
}
