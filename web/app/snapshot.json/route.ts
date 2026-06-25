import { getSnapshot } from "@/lib/data";

// Sirve el snapshot en /snapshot.json (desde el mismo web/data/snapshot.json que
// usa el dashboard — sin duplicar el archivo). Lo consume la CLI `precio` cuando
// corre fuera del repo (pip install en otra máquina). force-static => prebuild.
export const dynamic = "force-static";

export async function GET() {
  const snap = await getSnapshot();
  return new Response(JSON.stringify(snap), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "public, max-age=0, s-maxage=3600",
    },
  });
}
