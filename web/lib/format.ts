const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const MESES_LARGO = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

export const soles = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : `S/ ${n.toFixed(d)}`;

export const pct = (n: number | null | undefined) =>
  n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;

export const tons = (n: number | null | undefined) =>
  n == null ? "—" : `${n.toLocaleString("es-PE")} t`;

export const fechaCorta = (iso: string) => {
  const [, m, d] = iso.split("-");
  return `${+d} ${MESES[+m - 1]}`;
};

export const fechaLarga = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return `${+d} de ${MESES_LARGO[+m - 1]} de ${y}`;
};

/** color intent for a food-price move: cheaper = good (emerald), pricier = rose. */
export const moveClass = (v: number | null | undefined) =>
  v == null || v === 0
    ? "text-slate-400"
    : v > 0
      ? "text-rose-600"
      : "text-emerald-600";

export const moveBg = (v: number | null | undefined) =>
  v == null || v === 0
    ? "bg-slate-100 text-slate-500"
    : v > 0
      ? "bg-rose-50 text-rose-700"
      : "bg-emerald-50 text-emerald-700";

export const tendenciaClass = (t: string | null) => {
  switch (t) {
    case "En Alza":
      return "bg-rose-50 text-rose-700";
    case "En Baja":
      return "bg-emerald-50 text-emerald-700";
    case "Baja Notable":
      return "bg-emerald-100 text-emerald-800";
    default:
      return "bg-slate-100 text-slate-500";
  }
};
