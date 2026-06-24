import type { ResumenIA } from "@/lib/data";

type Props = {
  resumen?: ResumenIA;
};

/**
 * Tarjeta "Resumen del día". Muestra el texto de snapshot.resumenIA.
 * Si la fuente es 'fallback' (generado por reglas, sin API key de IA),
 * lo señala con un chip discreto y honesto.
 */
export default function AISummary({ resumen }: Props) {
  if (!resumen?.texto) return null;

  const esFallback = resumen.fuente === "fallback";

  return (
    <div className="h-full rounded-sm border border-rule bg-card p-5">
      <div className="flex items-center gap-2 mb-2">
        <h2 className="font-serif text-base text-ink">Resumen del día</h2>
        {esFallback ? (
          <span
            className="text-[10px] uppercase tracking-wide rounded-sm bg-ink/[0.05] text-muted px-1.5 py-0.5"
            title="Texto generado por reglas. Con una API key de IA, lo redacta Claude."
          >
            generado por reglas · IA con API key
          </span>
        ) : (
          <span className="text-[10px] uppercase tracking-wide rounded-sm bg-down/10 text-down px-1.5 py-0.5">
            redactado por IA
          </span>
        )}
      </div>
      <p className="text-ink leading-relaxed text-[15px]">{resumen.texto}</p>
    </div>
  );
}
