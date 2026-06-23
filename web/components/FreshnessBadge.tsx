import { fechaCorta } from "@/lib/format";

type Props = {
  /** Fecha del último dato disponible (ISO, p. ej. "2026-06-23"). */
  latestFecha: string;
  /** Momento en que se generó el snapshot (ISO con hora). */
  generatedAt: string;
  className?: string;
};

const MAX_HORAS = 48;

/**
 * Chip de frescura: "datos al <fecha>". Si el último dato tiene más de 48 h
 * respecto al momento de generación, marca el estado como "desactualizado".
 */
export default function FreshnessBadge({ latestFecha, generatedAt, className = "" }: Props) {
  // latestFecha es una fecha de calendario; la anclamos a fin del día UTC para
  // no penalizar por las horas dentro del propio día del último dato.
  const ultimo = new Date(`${latestFecha}T23:59:59Z`).getTime();
  const generado = new Date(generatedAt).getTime();

  const valido = Number.isFinite(ultimo) && Number.isFinite(generado);
  const horas = valido ? (generado - ultimo) / 3_600_000 : 0;
  const desactualizado = valido && horas > MAX_HORAS;

  const base =
    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium";
  const tono = desactualizado
    ? "bg-amber-50 text-amber-700 border border-amber-200"
    : "bg-emerald-50 text-emerald-700 border border-emerald-100";

  return (
    <span className={`${base} ${tono} ${className}`}>
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${
          desactualizado ? "bg-amber-500" : "bg-emerald-500"
        }`}
        aria-hidden
      />
      {desactualizado ? (
        <>desactualizado · datos al {fechaCorta(latestFecha)}</>
      ) : (
        <>datos al {fechaCorta(latestFecha)}</>
      )}
    </span>
  );
}
