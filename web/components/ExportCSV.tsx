type Props = {
  /** Cuántos productos hay. Solo decide si los enlaces se ofrecen o no. */
  nProductos: number;
};

/**
 * Enlaces de descarga de CSV.
 *
 * Antes esto era un componente de cliente que recibía el array de productos
 * COMPLETO y armaba el CSV en el navegador con `URL.createObjectURL`. Elegante,
 * pero el precio estaba escondido: para tener los datos disponibles "por si
 * acaso", Next serializaba las series enteras —524 puntos × 72 productos— dentro
 * del HTML de la portada. **3,43 MB de los 4,8 MB de la página**, en cada
 * visita, para un botón que casi nadie pulsa.
 *
 * Ahora son dos enlaces a `/csv/[modo]`, que se prerenderiza en el build y sirve
 * el CDN. Los datos viajan cuando alguien los pide, y ya en formato CSV.
 *
 * Deja de ser componente de cliente: sin `useCallback`, sin `Blob`, sin
 * JavaScript. Un enlace hace esto mejor que nosotros.
 */
export default function ExportCSV({ nProductos }: Props) {
  if (nProductos === 0) return null;

  const clase =
    "rounded-sm border border-rule bg-card px-3 py-1.5 text-xs font-medium " +
    "text-ink hover:border-ink transition-colors";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted">Descargar datos:</span>
      <a className={clase} href="/csv/latest" download>
        CSV · precios de hoy
      </a>
      <a className={clase} href="/csv/series" download>
        CSV · serie completa
      </a>
    </div>
  );
}
