import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Resolución manual del alias "@" -> raíz del paquete web (igual que tsconfig
// paths). No usamos vite-tsconfig-paths para no añadir dependencias extra.
const rootDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": rootDir,
    },
  },
  test: {
    // jsdom para que los tests que toquen el DOM (RTL) funcionen; los helpers
    // puros de lib/ corren igual sin tocar el DOM.
    environment: "jsdom",
    globals: true,
    include: ["test/**/*.test.{ts,tsx}"],
  },
});
