import type { MetadataRoute } from "next";
import { getSnapshot } from "@/lib/data";

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? "https://preciovivo.vercel.app";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const snap = await getSnapshot();
  const lastModified = new Date();
  return [
    { url: SITE, lastModified, changeFrequency: "daily", priority: 1 },
    ...snap.productos.map((p) => ({
      url: `${SITE}/p/${p.slug}`,
      lastModified,
      changeFrequency: "daily" as const,
      priority: 0.7,
    })),
  ];
}
