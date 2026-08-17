# Lanzar Precio Vivo

Arquitectura de producción: **sitio Next.js estático en Vercel** + **pipeline diario en tu máquina**.
El dashboard lee `web/data/snapshot.json` (hechos numéricos, versionado). El pipeline regenera ese
snapshot cada día y lo publica; Vercel redeploya solo.

## 1. Vercel (la URL pública)
1. Importa el repo en [vercel.com](https://vercel.com) → New Project.
2. **Root Directory: `web`** · Framework: Next.js (autodetectado).
3. **Environment Variables** (Project Settings → Environment Variables):
   - `NEXT_PUBLIC_SITE_URL` = `https://<tu-dominio-o-subdominio>.vercel.app` (para sitemap/OG/robots)
   - `AI_API_KEY` = tu clave de DeepSeek
   - `AI_BASE_URL` = `https://api.deepseek.com`
   - `AI_MODEL` = `deepseek-chat`
   - `EMBED_API_KEY`, `EMBED_BASE_URL`, `EMBED_MODEL`, `EMBED_DIMS` — **necesarias para el RAG**
4. Deploy. Listo: ya hay URL pública con los datos actuales.

> **Las variables `EMBED_*` van en Vercel, no solo en tu máquina.** El índice viaja en el deploy,
> pero la **pregunta** se embebe en cada request desde el servidor de Vercel. Si la clave vive solo
> en local, la firma no coincide, `/api/consulta` degrada a recuperación léxica y el sitio responde
> igual — sin búsqueda semántica y sin que nada falle a la vista. Los valores deben ser **los
> mismos** que construyeron `web/data/rag-*.bin`; si no, se compararían espacios vectoriales
> distintos. `pytest -m publicado` verifica esa coincidencia en CI antes de que llegue a producción.

### Opcional: apuntar el sitio a la API del pipeline
Si despliegas `preciovivo.api` en algún sitio, define en Vercel `PRECIOVIVO_API_URL` y
`PRECIOVIVO_API_KEY`. Entonces `/api/consulta` recupera el contexto llamando a `POST /recuperar` en
vez de usar el port de TypeScript de `web/lib/rag.ts` — es decir, la **misma** implementación que
las evaluaciones, la CLI y el servidor MCP. Si la API no responde a tiempo, el sitio cae al port
local: no se vuelve una dependencia dura.

## 2. DeepSeek (la capa IA)
- Saca una API key en [platform.deepseek.com](https://platform.deepseek.com).
- Ponla en Vercel (`AI_API_KEY`, paso 1) — habilita la **consulta en lenguaje natural**.
- Para que el **resumen del día** lo redacte el modelo (no el fallback), pon también la clave en
  tu `.env.local` local (la usa `preciovivo.export` al generar el snapshot). Ver `web/.env.example`.
- Sin clave, todo sigue funcionando en modo *fallback* (reglas + palabras clave).

## 3. Actualización diaria (los datos frescos)
El reporte del GMML sale cada día hábil. Para mantener el sitio al día:
1. Programa `scripts/actualizar-diario.ps1` en el **Programador de tareas de Windows** (diario, ~07:00).
   - Acción: `powershell.exe -ExecutionPolicy Bypass -File "C:\Trabajo\Clientes\PrecioVivo\scripts\actualizar-diario.ps1"`
2. El script: `ingest --latest 5` → `--forecast` (~14 min) → `--export` → `git push` del snapshot.
3. El push dispara un redeploy automático en Vercel. Sin cambios (fin de semana), no hace nada.

> **Por qué en tu máquina y no en CI:** el harvester baja de gob.pe; el WAF **no** bloquea tu IP,
> pero **sí** podría bloquear un runner de datacenter (GitHub Actions, etc.). Tu máquina es lo seguro.
> Alternativa: un VPS/proxy residencial en Perú.

## 4. Detalles finales
- **Contacto Árkos:** el footer ya enlaza a tu correo (`rodrigoan.torresp@gmail.com`) y a tu web
  (`árkos.com` → `https://www.xn--rkos-4na.com/`).
- **Dominio propio (opcional):** agrégalo en Vercel y actualiza `NEXT_PUBLIC_SITE_URL`.
- **Tamaño de git:** `snapshot.json` (~3 MB) se versiona y cambia a diario; el historial crece. Para
  un showcase está bien; si molesta, mover a Git LFS o deployar con `vercel deploy --prebuilt` (sin commitear).

## Local (desarrollo)
```
cd pipeline && .venv/Scripts/python -m preciovivo.ingest --reparse   # reconstruye DB desde PDFs cacheados
                .venv/Scripts/python -m preciovivo.ingest --forecast
                .venv/Scripts/python -m preciovivo.export
cd web && npm run dev          # http://localhost:3000  (build/test/lint también disponibles)
```
