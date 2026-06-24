import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

// Flat config para Next.js 16. `next lint` fue removido en v16; se usa la CLI
// de ESLint directamente (ver scripts package.json: "lint": "eslint .").
const eslintConfig = defineConfig([
  ...nextVitals,
  // Override de los ignores por defecto de eslint-config-next.
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
