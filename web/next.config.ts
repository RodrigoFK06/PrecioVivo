import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    // Logos de marca (Árkos) son SVG propios y confiables. Permitir que el
    // optimizador los sirva, con cabeceras que evitan ejecución de scripts.
    dangerouslyAllowSVG: true,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",
  },
};

export default nextConfig;
