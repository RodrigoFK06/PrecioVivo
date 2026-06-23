# Plan de Ejecución v4 — "Precio Vivo"
### Inteligencia de precios mayoristas con IA · Proyecto build-in-public de Árkos
> **v4 — 2026-06-23.** Corrige un error de datos de v2/v3 y conserva las mejoras del red-team.
> **Changelog clave:** v2/v3 concluyeron "el formato cambió y el volumen desapareció" — **falso**. Eso salió de analizar un PDF distinto (el *Boletín GMML + MM N°2*, una serie paralela hallada por búsqueda). Al bajar el **daily real de la colección 335 desde el archivo mensual**, se confirma que la **serie primaria no cambió**: sigue trayendo **Masa de Ingreso (t) = volumen, nativo y estructurado**. Esto **simplifica** el proyecto, valida tu instinto de v1, y vuelve **innecesario el spike EMMSA**. Se conservan intactas las patas que el red-team agregó (orden invertido, legal, distribución, operación, disciplina de forecasting).

---

## 0. Estado verificado (2026-06-22/23)
| Hecho | Detalle |
|---|---|
| Directorio | Estaba **vacío**; no había parser ni data. Fase 0 de cero. **Todo a git desde el día 1.** |
| **Formato primario** | **NO cambió.** Daily de colección 335 ("Reporte de ingreso y precios GMML") = formato clásico **con volumen** (§1). |
| **Volumen** | **Nativo y gratis** en el mismo PDF (columnas Masa de Ingreso) → **EMMSA innecesario; spike descartado.** La decisión previa "volumen vía EMMSA" queda **superada**. |
| WAF | **No bloquea esta máquina** → harvester local. Re-verificar si cambia IP de deploy. |
| Archivo | **≥2 años de profundidad**, no podado. Backfill factible (§5). |

### 0.1 Qué es y para quién (el comprador, explícito)
**Árkos vende productos de datos+IA a empresas medianas de LatAm.** Precio Vivo **no es un SaaS para los vendedores del mercado** — es el **showcase de capacidad** que prueba que Árkos convierte PDFs públicos sucios en un producto de IA vivo. El lead que importa: la empresa que ve el showcase y quiere software a medida.

### 0.5 Base legal de redistribución (bloqueante por fuente)
- Registrar por fuente: URL exacta · si está en `datosabiertos.gob.pe` (PNDA, **DS 016-2017-PCM**) · teoría de respaldo si no.
- **Regla operativa:** precios y tonelajes son **HECHOS no protegidos** → republicar **hechos numéricos normalizados**, **nunca** re-hostear el PDF fuente ni la prosa.
- **Atribución + disclaimer** en cada gráfico y respuesta de API: *"Fuente: MIDAGRI–GMML, procesado por Precio Vivo · cifras referenciales, no oficiales."*
- "Sin base legal documentada" = **bloquea** el ship de esa fuente.

---

## 1. Fuentes (verificadas por fetch directo)

### 1.1 PRIMARIA — "Reporte de ingreso y precios GMML" (colección 335)
- **Qué:** PDF **diario** (días hábiles) en `cdn.www.gob.pe`. ~67 productos, **un solo mercado (GMML)**, un día por archivo.
- **Columnas:** `PRODUCTO` · **`Masa de Ingreso (t)`: Ayer / Hoy / Últ.7 días / Últ.4 lunes** (= VOLUMEN) · `Unidad` · `Equiv. en Kg` · `Precio` Ayer / Hoy / Últ.7 (por unidad → **convertir a S//kg** con Equiv-Kg) · **`Tendencia`** (Estable / En Alza / En Baja / Baja Notable).
- **Navegación del harvester (mapeada):** colección 335 → página-mes (`informes-publicaciones/{id}-...-gmml-{mes}-{año}`, con `?sheet=2,3,4` para meses viejos) → **links directos a los PDF del CDN** (`.../file/{fileid}/{id}-...-DD-MM-YYYY.pdf`).
- **Pega coordenado obligatorio** (pdfplumber/pymupdf): `pdftotext -layout` desordena filas e incrusta la Tendencia dentro de la columna de precio.

### 1.2 SISAP MIDAGRI — cross-check (semana 2)
- URL **estable**, diaria (~06:30), multi-mercado **incl. POLLO VIVO**, precios S//kg. Se cablea como **gate de validación cruzada** (§4), no como fuente primaria.

### 1.3 Boletín GMML + MM N°2 — enriquecimiento opcional (Fase 2)
- Serie **separada y más rica** (multi-mercado + narrativa pre-escrita + S//kg + 7-día strip). Útil para sumar **MM N°2** y reusar la narrativa oficial. No es la primaria.

### 1.4 EMMSA — descartado para volumen
- SPV viejo 404 en host vivo; mirror 2021. **Innecesario** (volumen ya viene en 1.1). No se persigue.

---

## 2. Roadmap (orden invertido — el cambio del red-team, conservado)
> **Regla de oro:** *un extraño abre una URL pública y ve precios peruanos vivos, atribuidos y legalmente defendibles* debe ser TRUE antes de construir forecasting. (El volumen ya no es bloqueante — viene gratis — así que **se ingiere desde Fase 1**, pero los **modelos** de pronóstico siguen diferidos.)

- **Fase 0 — Reset (día 1).** ✅ git+repo · ✅ archivo mapeado · instalar pdfplumber · descargar muestras reales.
- **Fase 0.5 — Distribución y captura (en paralelo).** §7.
- **Fase 1 — Dashboard de precios VIVO y público (semana 1). El demo hero.**
  (a) parser coordenado del reporte 335 con **assertions write-blocking** (§4) → productos + **volumen** + precio S//kg + tendencia;
  (b) schema en **Supabase Postgres** (§3);
  (c) **~20 productos curados** de alto reconocimiento (papa, cebolla, tomate, limón, pollo-vía-SISAP luego, etc.);
  (d) dashboard Next.js/Vercel: precios de hoy + historial + **movers del día** + **resumen IA diario** + atribución/disclaimer + CTA Árkos;
  (e) **captura-forward** desde ya + backfill (§5).
  **Gate:** URL pública viva con datos reales. Cero forecasting hasta aquí.
- **Fase 2 — Enriquecimiento.** Multi-mercado vía Boletín (MM N°2) + SISAP cross-check. Explotar volumen para "señales de oferta".
- **Fase 3 — Forecasting honesto.** §6 (kill-gate; modelos reales gated por data).
- **Fase 4 — Pulido, alertas (email→WhatsApp), post héroe.**

---

## 3. Schema (Supabase Postgres directo)
- **`productos`** (id, nombre_canonico, categoria, unidad_default, **equiv_kg**) — diccionario para normalizar (~67 nombres estables; fuente única en semana 1 → normalización trivial). `producto_alias(alias_norm PK, producto_id, fuente)` se activa al sumar SISAP/Boletín (Fase 2).
- **`precios_diarios`** (fecha, producto_id, **masa_ayer, masa_hoy, masa_7d, masa_4lun**, unidad, equiv_kg, precio_ayer_unit, precio_hoy_unit, precio_7d_unit, **precio_hoy_kg** [computado], tendencia, fuente, cargado_en). PK `(producto_id, fecha)` — **idempotente** (un día por archivo; upsert simple). Mercado constante = GMML por ahora (columna `mercado_id` lista para Fase 2).
- **Diferida (Fase 3):** `pronosticos`.

---

## 4. Endurecimiento del parser (load-bearing)
**Assertions write-blocking desde la línea 1:** (a) conteo de filas/productos en banda (~67); (b) precio S//kg en rango sano (~0.2–60), outliers → cuarentena; (c) **checksum del texto+posición-x de encabezados** → si se mueven, HALT + alerta (tripwire real de cambio de formato); (d) **cross-check vs SISAP** (semana 2), delta >15% = flag; (e) **persistir PDF crudo (hash+fecha)** → trazabilidad + re-parseo; (f) **chequeo diario de salud de fuente** (URL 200 + archivo fechado fresco) en el canal de alertas.

---

## 5. Backfill
- **~120 PDF diarios = 6 meses** (días hábiles, ~20/mes). El archivo tiene **≥2 años** → factible incluso más. (El atajo "7-día strip / 26 archivos" era del Boletín, no de esta serie — no aplica.)
- **Captura-forward** desde ya + backfill por páginas-mes. Ajustar expectativas de forecasting a la historia real.
- **Nota de drift (verificada):** el parser order-based maneja el formato actual (2026) limpio. Ediciones **muy viejas (p.ej. 2024)** rinden el bloque de Masa de Ingreso en baselines verticales distintos al de precios → la asociación por fila falla. Backfill profundo (>1 año) requerirá una estrategia de asociación por banda para esas ediciones; no bloquea el MVP (que usa data actual + forward).

---

## 6. Volumen + Forecasting (con kill-gate)
- **Volumen:** ya disponible **gratis** (§1.1) → se ingiere desde Fase 1; alimenta features y "señales de oferta". El input de la tesis "volumen→precio" existe desde el día 1, sin riesgo.
- **§2.0 Hipótesis falsable (pre-registrada):** *"agregar volumen rezagado reduce el MAE walk-forward vs seasonal-naive en ≥X% sobre ≥N folds"*. Si falla → el forecast se queda en baseline; el volumen se muestra como contexto, no como predictor.
- **Modelos:** cortar Prophet/GBM del MVP; escalera tope en seasonal-naive → media-móvil + tendencia. GBM/Prophet gated por **≥18–24 meses** de data. Métrica única = **walk-forward CV de ventana expansiva**; **guard de CI** anti-leakage; productos con <90 obs → sin pronóstico. Reencuadre del claim: *"el volumen marca días de volatilidad/abastecimiento anómalo."*

---

## 7. Distribución y captura (en paralelo desde Fase 0.5)
1. **Branding Árkos + CTA** en el dashboard desde el día 1.
2. **≥1 página server-rendered indexable** ("Precio de la papa/cebolla/tomate hoy en Lima", auto-actualizada) para alcance orgánico + AI-search. **Correr la skill `geo`.**
3. **Canal:** LinkedIn LatAm en español.
4. **Métrica única:** consultas de agencia calificadas atribuibles en 60 días.

---

## 8. Capa IA (Claude)
- **Resumen diario:** Claude narra los movers + volumen ("la cebolla entró 1.410 t vs 667 de promedio — fuerte ingreso, precio estable"). *No almacenar prosa externa; generar desde nuestros hechos.*
- **Anomalías** + **consulta en lenguaje natural** sobre Supabase.

---

## 9. Arquitectura
```
[colección 335: páginas-mes → PDFs CDN] ─► Harvester+Parser (Python, coordenado, LOCAL, assertions §4)
                                               │ productos · volumen · precio→S//kg · tendencia
                                               ▼
                                         Supabase Postgres  ◄── public URL (lead-gen)
                                           │           │
                                    Forecast (Fase 3)  API routes Next.js ─► Claude API
                                                       ▼
                                                   Dashboard Vercel + atribución/disclaimer + CTA Árkos
```
- **Supabase directo** (sin capa de swap). `.env` local para dev. Free-tier: techo 500 MB + auto-pausa 7 días (retry/keep-alive en el harvester).

---

## 10. Riesgos
| Riesgo | Mitigación |
|---|---|
| Legal/licencia | §0.5: solo hechos, sin re-host, atribución+disclaimer. |
| Cambio de formato (riesgo real a futuro) | Tripwire de checksum de encabezados + PDF crudo guardado + SISAP cross-check. |
| Forecast sobre poca data | Cortado del MVP; kill-gate; walk-forward CV; reencuadre. |
| Supabase free-tier | Retry/keep-alive + sizing. |
| Mantenimiento/bus-factor | §11. |

---

## 11. Operación y mantenimiento
- **Mantenedor nombrado** + ~1–3 h/sem (drift del parser).
- **Badge público de frescura:** >48 h stale → lo dice (falla = señal de credibilidad).
- **Degradación elegante + sunset/handoff** a caso de estudio archivado.
- **Todo a git desde el día 1.**

---

## 12. Decisiones — CERRADAS
1. **Inversión de orden: ACEPTADA.** Dashboard vivo en semana 1; modelos de forecasting → Fase 3.
2. **Nombre: "Precio Vivo".** Repo/dominio: `precio-vivo` / `preciovivo`.
3. **Tácticas baked-in:** Supabase directo · GMML + ~20 productos en semana 1 · parser del reporte 335 primero.
4. **Volumen:** se ingiere desde Fase 1 (gratis en el PDF). **"Volumen vía EMMSA" queda superado** — EMMSA descartado.

→ Plan firmado y corregido. Ejecutando Fase 1: parser del reporte 335.
