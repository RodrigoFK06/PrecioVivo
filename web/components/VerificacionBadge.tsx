import { type Verificacion } from "@/lib/data";
import { fechaCorta } from "@/lib/format";

type Props = {
  verificacion?: Verificacion;
  className?: string;
};

/**
 * Chip de verificación cruzada: "contrastado con SISAP · n/m coinciden".
 * SISAP (MIDAGRI) es una publicación independiente del mismo día; el chip
 * resume cuántos precios GMML coinciden dentro del umbral (±15%).
 * No se muestra si no hay contraste o si el contraste no es del día vigente
 * (un check viejo diría "verificado" sobre datos que no verificó).
 */
export default function VerificacionBadge({ verificacion, className = "" }: Props) {
  if (
    !verificacion ||
    !verificacion.vigente ||
    !verificacion.gmml_disponible ||
    verificacion.contrastados === 0
  ) {
    return null;
  }
  const { coinciden, contrastados, fecha } = verificacion;
  const todos = coinciden === contrastados;

  const base =
    "inline-flex items-center gap-1.5 rounded-sm px-3 py-1 text-xs font-medium";
  const tono = todos
    ? "bg-down/10 text-down border border-down/20"
    : "bg-ink/5 text-muted border border-rule";

  return (
    <span
      className={`${base} ${tono} ${className}`}
      title={`Contraste independiente vía SISAP–MIDAGRI del ${fecha ? fechaCorta(fecha) : "día"}: ${coinciden} de ${contrastados} precios GMML coinciden dentro de ±${verificacion.umbral_pct}%.`}
    >
      <span aria-hidden>✓</span>
      contrastado con SISAP · {coinciden}/{contrastados} coinciden
    </span>
  );
}
