import type { MetadataRoute } from "next";

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? "https://preciovivo.vercel.app";

/**
 * AEO Kit — se nombran uno a uno los rastreadores de búsqueda y de IA.
 * Varios de estos agentes se abstienen ante un robots.txt que solo trae el
 * comodín, y son los que alimentan las citas en ChatGPT, Claude, Perplexity,
 * Gemini y Google AI Overviews.
 */
const AGENTES_IA_Y_BUSQUEDA = [
  "Googlebot",
  "Bingbot",
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  "ClaudeBot",
  "Claude-User",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Applebot-Extended",
  "Meta-ExternalAgent",
  "CCBot",
  "Amazonbot",
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: "*", allow: "/" },
      ...AGENTES_IA_Y_BUSQUEDA.map((userAgent) => ({ userAgent, allow: "/" })),
    ],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
