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
4. Deploy. Listo: ya hay URL pública con los datos actuales.

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
- **Contacto Árkos:** actualiza el `mailto:` en `web/app/layout.tsx` (hoy `hola@arkos.dev`, placeholder).
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
