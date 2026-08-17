import { getSnapshot, submuestrear, type Point } from "@/lib/data";

/**
 * Serie histórica de UN producto, prerenderizada y servida por el CDN.
 *
 * POR QUÉ EXISTE
 * --------------
 * El comparador grafica entre 2 y 5 productos, pero recibía los 72 con 140
 * puntos cada uno serializados en el HTML de la portada: **0,86 MB en cada
 * visita** para una herramienta que está bajo el pliegue y que la mayoría no
 * llega a tocar.
 *
 * Partido por producto, quien compara dos descarga ~24 KB y quien no compara
 * nada no descarga nada. Son 72 ficheros estáticos generados en el build: sin
 * trabajo por petición, cacheables uno a uno por el navegador y por el CDN.
 *
 * La resolución es la misma que consumía el comparador (140 puntos), así que el
 * gráfico se ve igual; lo único que cambia es CUÁNDO viajan los datos.
 */
export const dynamic = "force-static";

/** Espeja la resolución que usaba la portada. */
const MAX_PUNTOS = 140;

export async function generateStaticParams() {
  const snap = await getSnapshot();
  return snap.productos.map((p) => ({ slug: p.slug }));
}

export async function GET(_req: Request, ctx: { params: Promise<{ slug: string }> }) {
  const { slug } = await ctx.params;
  const snap = await getSnapshot();
  const producto = snap.productos.find((p) => p.slug === slug);

  if (!producto) {
    return new Response(JSON.stringify({ error: `no existe el producto '${slug}'` }), {
      status: 404,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  }

  const series: Point[] = submuestrear(producto.series, MAX_PUNTOS);
  return new Response(
    JSON.stringify({ slug: producto.slug, nombre: producto.nombre, series }),
    {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "public, max-age=0, s-maxage=3600",
      },
    },
  );
}
