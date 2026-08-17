import { resultadosVerificacion, type Verificacion } from "@/lib/data";
import { soles, pct, fechaLarga } from "@/lib/format";

type Props = {
  verificacion?: Verificacion;
};

/**
 * Panel de verificación cruzada (metodología): los precios GMML del día
 * contrastados contra SISAP–MIDAGRI, una publicación independiente del mismo
 * ministerio. Muestra el detalle honesto: coincidencias, divergencias sobre el
 * umbral y productos sin contraparte. Si no hay contraste, no renderiza nada.
 */
export default function VerificacionPanel({ verificacion }: Props) {
  if (!verificacion || verificacion.contrastados === 0) return null;
  const v = verificacion;
  const filas = resultadosVerificacion(v);
  const divergentes = filas.filter((r) => r.flag && r.gmml_kg != null).length;
  const sinPar = filas.filter((r) => r.gmml_kg == null).length;

  return (
    <div>
      <h3 className="text-base font-semibold tracking-tight text-ink">
        Verificación cruzada con SISAP
      </h3>
      <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">
        Cada día contrastamos nuestros precios GMML contra el reporte SISAP de
        MIDAGRI — una publicación independiente con los mismos productos. Si un
        precio difiere en más de ±{v.umbral_pct}%, lo marcamos aquí en lugar de
        esconderlo.{" "}
        {v.fecha && (
          <span className="text-faint">Último contraste: {fechaLarga(v.fecha)}.</span>
        )}
      </p>

      {!v.gmml_disponible ? (
        <p className="mt-4 rounded-md border border-rule bg-card p-4 text-sm leading-relaxed text-muted">
          SISAP ya publicó su reporte
          {(v.fecha_sisap ?? v.fecha) && <> del {fechaLarga((v.fecha_sisap ?? v.fecha)!)}</>},
          pero no había ningún día GMML en nuestra base con el cual contrastarlo.{" "}
          {v.contrastados} producto(s) quedan en espera; el contraste se completa en la
          siguiente actualización.
        </p>
      ) : (
        <ResultadosTabla v={v} filas={filas} divergentes={divergentes} sinPar={sinPar} />
      )}
    </div>
  );
}

function ResultadosTabla({
  v,
  filas,
  divergentes,
  sinPar,
}: {
  v: Verificacion;
  filas: ReturnType<typeof resultadosVerificacion>;
  divergentes: number;
  sinPar: number;
}) {
  return (
    <div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[480px] text-sm tabular-nums">
          <thead>
            <tr className="border-b border-rule text-left">
              <th className="eyebrow py-2 pr-3 font-medium">Producto</th>
              <th className="eyebrow py-2 pr-3 text-right font-medium">Precio Vivo</th>
              <th className="eyebrow py-2 pr-3 text-right font-medium">SISAP</th>
              <th className="eyebrow py-2 text-right font-medium">Δ</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {filas.map((r) => (
              <tr key={r.producto}>
                <td className="py-1.5 pr-3 text-ink">
                  {r.producto.toLowerCase()}
                  {r.flag && (
                    <span
                      className="ml-2 rounded-sm bg-up/10 px-1.5 py-0.5 text-[11px] font-medium text-up"
                      title={r.detalle}
                    >
                      {r.gmml_kg == null ? "sin contraparte" : "divergente"}
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right text-muted">{soles(r.gmml_kg)}</td>
                <td className="py-1.5 pr-3 text-right text-muted">{soles(r.sisap_kg)}</td>
                <td
                  className={`py-1.5 text-right ${r.flag ? "font-medium text-up" : "text-faint"}`}
                >
                  {pct(r.delta_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-3 text-xs leading-relaxed text-faint">
        {v.coinciden} de {v.contrastados} coinciden dentro de ±{v.umbral_pct}%
        {divergentes > 0 && <> · {divergentes} divergente(s)</>}
        {sinPar > 0 && <> · {sinPar} sin contraparte en nuestra base ese día</>}. SISAP
        es una segunda opinión, no nuestra fuente primaria: ambas series salen de
        MIDAGRI, pero por canales distintos — si divergen, algo merece revisión.
        {v.base && (
          <>
            {" "}
            <span className="text-muted">{v.base}</span>
          </>
        )}
      </p>
    </div>
  );
}
