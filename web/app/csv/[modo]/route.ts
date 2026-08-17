import { getSnapshot, productosToCSV } from "@/lib/data";

/**
 * CSV de descarga, generado en el SERVIDOR y prerenderizado.
 *
 * POR QUÉ EXISTE
 * --------------
 * Antes el CSV se armaba en el navegador, lo que obligaba a mandarle a
 * `ExportCSV` el array de productos ENTERO —las series completas, 524 puntos ×
 * 72 productos— serializado dentro del HTML de la portada. Medido: **3,43 MB de
 * los 4,8 MB que pesaba la página**, embarcados en cada visita para alimentar un
 * botón que la mayoría no pulsa. En una conexión móvil peruana eso es la
 * diferencia entre una portada que carga y una que no.
 *
 * Aquí los datos no viajan hasta que alguien los pide, y cuando los pide llegan
 * como CSV y no como JSON dentro del HTML.
 *
 * `force-static` + `generateStaticParams`: los dos ficheros se generan en el
 * build y los sirve el CDN. Cero trabajo por petición — el snapshot solo cambia
 * con un deploy nuevo, así que no hay nada que recalcular en caliente.
 */
export const dynamic = "force-static";

const MODOS = { latest: "precios-hoy", series: "serie-completa" } as const;
type Modo = keyof typeof MODOS;

export function generateStaticParams() {
  return Object.keys(MODOS).map((modo) => ({ modo }));
}

export async function GET(_req: Request, ctx: { params: Promise<{ modo: string }> }) {
  const { modo } = await ctx.params;
  if (!(modo in MODOS)) {
    return new Response("modo debe ser 'latest' o 'series'", { status: 404 });
  }

  const snap = await getSnapshot();
  // BOM UTF-8: sin él, Excel destroza los acentos de los nombres.
  const csv = "﻿" + productosToCSV(snap.productos, modo as Modo);
  const nombre = `precio-vivo-${MODOS[modo as Modo]}-${snap.latestFecha}.csv`;

  return new Response(csv, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      // El nombre lleva la fecha del DATO, no la del reloj: dos descargas del
      // mismo archivo en días distintos deben poder distinguirse por su
      // contenido, no por cuándo se pulsó el botón.
      "content-disposition": `attachment; filename="${nombre}"`,
      "cache-control": "public, max-age=0, s-maxage=3600",
    },
  });
}
