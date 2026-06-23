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
    <div className="h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 mb-2">
        <h2 className="text-sm font-semibold">Resumen del día</h2>
        {esFallback ? (
          <span
            className="text-[10px] uppercase tracking-wide rounded bg-slate-100 text-slate-500 px-1.5 py-0.5"
            title="Texto generado por reglas. Con una API key de IA, lo redacta Claude."
          >
            generado por reglas · IA con API key
          </span>
        ) : (
          <span className="text-[10px] uppercase tracking-wide rounded bg-emerald-50 text-emerald-700 px-1.5 py-0.5">
            redactado por IA
          </span>
        )}
      </div>
      <p className="text-slate-700 leading-relaxed text-[15px]">{resumen.texto}</p>
    </div>
  );
}
