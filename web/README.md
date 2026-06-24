# Precio Vivo — web

Showcase de **Árkos**: panel de precios mayoristas de alimentos frescos del
**Gran Mercado Mayorista de Lima (GMML)**, con tendencias, anomalías de
oferta/precio y pronóstico a 1 día.

Stack: **Next.js 16** (App Router, Turbopack) · **React 19** · **Tailwind v4** ·
**TypeScript**. Solo código local; los datos se leen de `data/snapshot.json`.

## Requisitos

- Node.js 20+ y npm.
- Dependencias instaladas: `npm install` (una sola vez).

## Comandos

Todos se ejecutan desde esta carpeta (`web/`).

```bash
npm run dev     # servidor de desarrollo (http://localhost:3000)
npm run build   # build de producción (Turbopack)
npm start       # sirve el build de producción
npm run test    # tests unitarios (Vitest, una pasada sin watch)
npm run lint    # ESLint (eslint-config-next / core-web-vitals)
```

### Tests

Los tests viven en `test/` y usan **Vitest** + **jsdom** + Testing Library.
Cubren los helpers puros de `lib/format.ts` (`soles`, `pct`, `tons`, `fechaCorta`,
`fechaLarga`) y de `lib/data.ts` (`buscarProductos` —insensible a acentos—,
`productosToCSV`, `promedioMensualHistorico`), tanto con fixtures mínimos como
contra el `data/snapshot.json` real.

```bash
npm run test            # corre todo una vez
npx vitest              # modo watch durante el desarrollo
npx vitest run format   # filtra por nombre de archivo de test
```

El alias `@` apunta a la raíz de `web/` (igual que en `tsconfig.json`),
configurado en `vitest.config.ts`.

### Lint

`next lint` fue removido en Next.js 16; se usa la CLI de ESLint directamente
(`eslint .`) con flat config en `eslint.config.mjs`.

## Variables de entorno

Copia `.env.example` a `.env.local` y completa lo que necesites. Todo es
**opcional** en modo showcase local:

- `ANTHROPIC_API_KEY` — habilita el resumen IA y la consulta en lenguaje
  natural. Sin ella, la app usa el texto de respaldo (`fuente="fallback"`).
- `DATABASE_URL` — cadena de conexión para el swap de producción de
  `lib/data.ts`. No se necesita en local (se lee `data/snapshot.json`).

## Datos

En desarrollo, `lib/data.ts` (`getSnapshot()`) lee `data/snapshot.json`
server-side. Ese archivo es el único punto de cambio para conectar a
Supabase/Postgres en producción.

Sobre el pronóstico, dos cosas distintas y ambas honestas:

1. **Modelo IA vs baseline.** El modelo de IA (GradientBoosting con lags,
   calendario y feriados, validado walk-forward anti-leakage) **le gana** al
   baseline ingenuo en la mayoría de productos, con ~50 % menos de error. En
   esos productos `forecast.metodo == "gbm"`.
2. **Volumen vs sin-volumen.** Añadir el **volumen** específicamente **no**
   mejora la predicción del precio (`forecastMeta.kill_gate.volume_helps =
   false`). El volumen se usa como señal de oferta/anomalía, no como predictor.

Se muestran MAE e intervalos reales; no hay cifras inventadas.

## Fuente y atribución

> Fuente: MIDAGRI – GMML, procesado por Precio Vivo · cifras referenciales, no
> oficiales.

Los precios provienen del **Ministerio de Desarrollo Agrario y Riego (MIDAGRI)**
para el **Gran Mercado Mayorista de Lima**. Son cifras **referenciales, no
oficiales**, reprocesadas por Precio Vivo. La atribución debe mantenerse visible
en la interfaz. Precio Vivo es un showcase de **Árkos**.
