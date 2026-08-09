# Dashboard de Precio Vivo (Next.js 16).
#
# Multi-stage: la imagen final no lleva ni el código fuente ni las dependencias
# de build. Con `output: "standalone"` en next.config.ts, Next arma un bundle con
# solo los node_modules que de verdad se usan en runtime.
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` y no `npm install`: instala exactamente el lockfile, que es lo que
# hace reproducible el build.
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# El snapshot y el índice RAG se copian con el resto: el sitio los lee del disco
# en build y en runtime (ver web/lib/data.ts y web/lib/rag.ts).
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Usuario sin privilegios (node ya existe en la imagen base con uid 1000).
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
# `data/` va aparte: standalone no arrastra archivos que no importa el bundler,
# y snapshot.json + rag-*.bin se leen en runtime con fs.
COPY --from=build --chown=node:node /app/data ./data

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
