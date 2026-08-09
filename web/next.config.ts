import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Bundle autocontenido para el contenedor (ver web/Dockerfile): Next arma
  // .next/standalone con solo los node_modules que se usan en runtime, así la
  // imagen final no lleva ni el código fuente ni las dependencias de build.
  // Vercel ignora esta opción y usa su propio empaquetado, así que no le afecta.
  output: "standalone",
  images: {
    // Logos de marca (Árkos) son SVG propios y confiables. Permitir que el
    // optimizador los sirva, con cabeceras que evitan ejecución de scripts.
    dangerouslyAllowSVG: true,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",
  },
};

export default nextConfig;
