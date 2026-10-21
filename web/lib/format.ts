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

/**
 * Nombre de producto para mostrar. El reporte fuente (y el pipeline, con
 * `.title()`) capitaliza todo: "Ajo Criollo O Napuri". Aquí los conectores
 * cortos van en minúscula salvo al inicio. Solo presentación: el nombre
 * canónico (clave de la BD, del slug y de los CSV) no se toca. Los nombres en
 * MAYÚSCULAS de otros mercados se dejan tal cual.
 */
const CONECTORES = new Set(["o", "u", "y", "e", "de", "del", "la", "las", "el", "los", "en", "con", "sin", "al"]);
export const nombreProducto = (nombre: string) =>
  nombre === nombre.toUpperCase()
    ? nombre
    : nombre
        .split(" ")
        .map((w, i) => (i > 0 && CONECTORES.has(w.toLowerCase()) ? w.toLowerCase() : w))
        .join(" ");

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
    ? "text-faint"
    : v > 0
      ? "text-up"
      : "text-down";

export const moveBg = (v: number | null | undefined) =>
  v == null || v === 0
    ? "bg-ink/5 text-muted"
    : v > 0
      ? "bg-up/10 text-up"
      : "bg-down/10 text-down";

export const tendenciaClass = (t: string | null) => {
  switch (t) {
    case "En Alza":
      return "bg-up/10 text-up";
    case "En Baja":
      return "bg-down/10 text-down";
    case "Baja Notable":
      return "bg-down/15 text-down";
    default:
      return "bg-ink/5 text-muted";
  }
};
