"use client";

import { useCallback, useEffect, useState } from "react";

/* Guía interactiva: tour con overlay sombreado (spotlight) + glosario "cómo leer".
   Mobile-first: el spotlight resalta el elemento y la explicación va en una tarjeta
   fija abajo (bottom-sheet), que funciona igual en móvil y escritorio. */

type Paso = { sel: string; titulo: string; cuerpo: string };

const PASOS: Paso[] = [
  {
    sel: '[data-guia="cifras"]',
    titulo: "Lo más importante, en una línea",
    cuerpo:
      "Predecimos los precios con IA. Acá ves en cuántos productos el modelo le gana al baseline y cuánto menos error tiene. «Baseline» = el método ingenuo (mañana ≈ hoy). «Walk-forward» = lo probamos usando solo el pasado, sin mirar el futuro.",
  },
  {
    sel: '[data-guia="resumen"]',
    titulo: "El resumen del día",
    cuerpo:
      "Un resumen automático de la jornada: qué entró fuerte, qué subió y qué bajó. Se genera con reglas sobre los datos reales (o con IA si hay API key) — nunca inventa cifras.",
  },
  {
    sel: '[data-guia="consulta"]',
    titulo: "Pregunta en tu idioma",
    cuerpo:
      "Escribe una pregunta normal, p. ej. «¿qué está más barato hoy?» o «precio de la papa». Responde solo con datos del mercado.",
  },
  {
    sel: '[data-guia="movers"]',
    titulo: "Qué se movió hoy",
    cuerpo:
      "Lo que más cambió frente a ayer: más caros (rojo), más baratos (verde) y dónde entró más volumen (toneladas de ingreso al mercado).",
  },
  {
    sel: '[data-guia="tabla"]',
    titulo: "Cómo leer la tabla",
    cuerpo:
      "Cada fila es un producto: precio en S/ por kg, variación vs ayer (Δ, rojo sube / verde baja), tendencia, ingreso del día en toneladas y el pronóstico de mañana con IA. Busca arriba o desplázate dentro de la tabla; haz clic en un producto para ver su historia.",
  },
  {
    sel: '[data-guia="metodologia"]',
    titulo: "Honestidad, sin trucos",
    cuerpo:
      "Mostramos qué funciona y qué no: el modelo de IA le gana al baseline (sí), pero el volumen por sí solo no ayuda a predecir el precio (no). Con los números reales de cada caso.",
  },
];

const GLOSARIO: { t: string; d: string }[] = [
  { t: "S/ por kg", d: "Precio mayorista normalizado a soles por kilogramo." },
  { t: "Δ ayer (variación)", d: "Cuánto cambió el precio hoy respecto a ayer, en %. Rojo = subió, verde = bajó." },
  { t: "Tendencia", d: "Etiqueta del reporte oficial: Estable, En Alza, En Baja o Baja Notable." },
  { t: "Ingreso (t)", d: "Toneladas del producto que entraron hoy al mercado — la oferta del día." },
  { t: "Pronóstico con IA", d: "Estimado del precio de mañana con un modelo (Gradient Boosting) entrenado con su historia. Es beta." },
  { t: "MAE", d: "Error absoluto medio del pronóstico, en S//kg. Más bajo = mejor." },
  { t: "Baseline", d: "Método ingenuo de comparación (≈ «mañana = hoy»). El modelo es útil solo si le gana." },
  { t: "Walk-forward", d: "Validación que en cada punto usa solo el pasado, nunca el futuro. Evita el autoengaño." },
  { t: "Anomalía (σ / z)", d: "Cuánto se desvía un valor de su historia reciente, en desviaciones estándar. |z| ≥ 3,5 es raro." },
  { t: "Watchlist", d: "Productos que marcas para seguir y filtrar sus alertas." },
];

const VISTA_KEY = "pv-guia-vista-v1";

export default function Guia() {
  const [modo, setModo] = useState<"off" | "bienvenida" | "tour" | "glosario">("off");
  const [paso, setPaso] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);

  // Primera visita: ofrece el tour automáticamente (una sola vez). La detección
  // necesita localStorage (solo cliente), así que el setState va en el efecto de
  // montaje a propósito — no es un sync derivable en render (rompería SSR/hidratación).
  useEffect(() => {
    try {
      if (!localStorage.getItem(VISTA_KEY)) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setModo("bienvenida");
      }
    } catch {
      /* localStorage no disponible: el botón flotante sigue ahí */
    }
  }, []);

  const cerrar = useCallback(() => {
    setModo("off");
    try {
      localStorage.setItem(VISTA_KEY, "1");
    } catch {
      /* noop */
    }
  }, []);

  // Rastrea el objetivo del paso EN VIVO: recalcula su posición en cada scroll y
  // resize (vía requestAnimationFrame), de modo que el spotlight siga al elemento
  // aunque el usuario se desplace o el scroll suave aún esté en curso. Todo el
  // setState ocurre dentro de `medir` (rAF/listeners), nunca síncrono en el efecto.
  useEffect(() => {
    if (modo !== "tour") return;
    const el = document.querySelector(PASOS[paso]?.sel ?? "") as HTMLElement | null;
    let raf = 0;
    const medir = () => {
      raf = 0;
      setRect(el ? el.getBoundingClientRect() : null);
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(medir);
    };
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    schedule(); // primera medición (diferida un frame)
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Re-medir durante ~0.9s para capturar el asentamiento del scroll suave.
    const iv = window.setInterval(schedule, 100);
    const stop = window.setTimeout(() => window.clearInterval(iv), 900);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      window.clearInterval(iv);
      window.clearTimeout(stop);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [modo, paso]);

  // Cerrar con Escape durante el tour/glosario.
  useEffect(() => {
    if (modo === "off") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cerrar();
      if (modo === "tour" && e.key === "ArrowRight") setPaso((p) => Math.min(p + 1, PASOS.length - 1));
      if (modo === "tour" && e.key === "ArrowLeft") setPaso((p) => Math.max(p - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modo, cerrar]);

  return (
    <>
      {/* Botón flotante: siempre disponible para re-abrir la guía. */}
      {modo === "off" && (
        <button
          type="button"
          onClick={() => setModo("bienvenida")}
          className="fixed bottom-4 right-4 z-40 inline-flex items-center gap-2 border border-ink bg-ink px-4 py-2.5 text-sm font-medium text-paper shadow-lg hover:opacity-90 transition-opacity"
          aria-label="Qué es Precio Vivo y cómo leerlo"
        >
          <span aria-hidden className="text-base leading-none">?</span>
          Qué es esto
        </button>
      )}

      {/* ── Bienvenida: qué es / para qué sirve / quiénes (Árkos) ── */}
      {modo === "bienvenida" && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-ink/60 p-3 sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Bienvenida a Precio Vivo"
          onClick={cerrar}
        >
          <div
            className="w-full max-w-xl max-h-[88vh] overflow-auto border border-rule bg-card p-6 sm:p-8 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="eyebrow">Precio Vivo · por Árkos</p>
              <button
                type="button"
                onClick={cerrar}
                className="text-sm text-muted hover:text-ink transition-colors"
                aria-label="Cerrar"
              >
                ✕
              </button>
            </div>
            <h2 className="font-serif text-2xl sm:text-3xl font-semibold tracking-tight text-ink mt-2">
              Los precios del mercado, vivos y con IA
            </h2>

            <div className="mt-4 space-y-4 text-[15px] text-muted leading-relaxed">
              <p>
                <span className="text-ink font-medium">Qué es.</span> Tomamos los reportes diarios
                del Gran Mercado Mayorista de Lima (MIDAGRI) —PDFs que casi nadie aprovecha— y los
                volvemos una serie de precios limpia, con 2 años de historia, que se actualiza cada
                día.
              </p>
              <p>
                <span className="text-ink font-medium">Para qué sirve.</span> Para ver cuánto cuesta
                hoy cada producto, qué subió y qué bajó, cómo viene la tendencia y una predicción del
                precio de mañana con IA. Útil para compradores (restaurantes, cadenas, mayoristas),
                prensa y para cualquiera al que le importe el precio de la comida.
              </p>
              <p>
                <span className="text-ink font-medium">Quiénes lo hacemos.</span> Lo construye{" "}
                <span className="text-ink font-medium">Árkos</span> — convertimos data pública y
                desordenada en productos de datos + IA. Esto es nuestro showcase, hecho en público y
                con honestidad sobre qué funciona y qué no.
              </p>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-rule pt-5">
              <button
                type="button"
                onClick={() => {
                  setPaso(0);
                  setModo("tour");
                }}
                className="border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper hover:opacity-90 transition-opacity"
              >
                Hacer el tour
              </button>
              <button
                type="button"
                onClick={() => setModo("glosario")}
                className="border border-rule px-3 py-2 text-sm text-ink hover:border-ink transition-colors"
              >
                Cómo leer
              </button>
              <button
                type="button"
                onClick={cerrar}
                className="ml-auto text-sm text-muted hover:text-ink transition-colors"
              >
                Explorar el tablero →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Tour con spotlight sombreado ──────────────────────────── */}
      {modo === "tour" && (
        <div
          className="pointer-events-none fixed inset-0 z-50"
          role="dialog"
          aria-modal="true"
          aria-label="Tour guiado"
        >
          {/* Capa que oscurece todo menos el objetivo (box-shadow gigante = el "agujero").
              Sin transición de posición: sigue al scroll al instante, sin arrastrarse. */}
          {rect && rect.width > 0 ? (
            <div
              className="absolute rounded-sm"
              style={{
                top: rect.top - 8,
                left: rect.left - 8,
                width: rect.width + 16,
                height: rect.height + 16,
                boxShadow: "0 0 0 9999px rgba(12,12,13,0.62)",
                outline: "2px solid rgba(255,255,255,0.9)",
                outlineOffset: "0px",
              }}
            />
          ) : (
            <div className="absolute inset-0 bg-ink/60" />
          )}

          {/* Tarjeta de explicación: fija abajo (bottom-sheet, mobile-first). */}
          <div className="pointer-events-auto absolute inset-x-0 bottom-0 p-3 sm:p-4">
            <div className="mx-auto max-w-lg border border-rule bg-card p-5 shadow-2xl">
              <div className="flex items-center justify-between gap-3">
                <p className="eyebrow">
                  Paso {paso + 1} de {PASOS.length}
                </p>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setModo("glosario")}
                    className="text-xs text-muted hover:text-accent transition-colors"
                  >
                    Glosario
                  </button>
                  <button
                    type="button"
                    onClick={cerrar}
                    className="text-xs text-muted hover:text-ink transition-colors"
                  >
                    Saltar ✕
                  </button>
                </div>
              </div>

              <h3 className="font-serif text-xl text-ink mt-2">{PASOS[paso].titulo}</h3>
              <p className="mt-1.5 text-sm text-muted leading-relaxed">{PASOS[paso].cuerpo}</p>

              <div className="mt-4 flex items-center justify-between gap-2">
                <div className="flex gap-1" aria-hidden>
                  {PASOS.map((_, i) => (
                    <span
                      key={i}
                      className={`h-1.5 w-1.5 rounded-full ${i === paso ? "bg-accent" : "bg-rule-strong"}`}
                    />
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  {paso > 0 && (
                    <button
                      type="button"
                      onClick={() => setPaso((p) => Math.max(p - 1, 0))}
                      className="border border-rule px-3 py-1.5 text-sm text-ink hover:border-ink transition-colors"
                    >
                      Atrás
                    </button>
                  )}
                  {paso < PASOS.length - 1 ? (
                    <button
                      type="button"
                      onClick={() => setPaso((p) => Math.min(p + 1, PASOS.length - 1))}
                      className="border border-ink bg-ink px-3 py-1.5 text-sm font-medium text-paper hover:bg-accent hover:border-accent transition-colors"
                    >
                      Siguiente →
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={cerrar}
                      className="border border-ink bg-ink px-4 py-1.5 text-sm font-medium text-paper hover:bg-accent hover:border-accent transition-colors"
                    >
                      Entendido
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Glosario "cómo leer" ─────────────────────────────────── */}
      {modo === "glosario" && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-ink/60 p-3 sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Glosario"
          onClick={cerrar}
        >
          <div
            className="w-full max-w-lg max-h-[85vh] overflow-auto border border-rule bg-card p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 border-b border-rule pb-3">
              <h3 className="font-serif text-2xl text-ink">Cómo leer el tablero</h3>
              <button
                type="button"
                onClick={cerrar}
                className="text-sm text-muted hover:text-ink transition-colors"
                aria-label="Cerrar"
              >
                ✕
              </button>
            </div>
            <dl className="mt-4 space-y-3.5">
              {GLOSARIO.map((g) => (
                <div key={g.t} className="grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-x-4 gap-y-0.5">
                  <dt className="font-medium text-ink">{g.t}</dt>
                  <dd className="text-sm text-muted leading-relaxed">{g.d}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-5 flex justify-end gap-2 border-t border-rule pt-4">
              <button
                type="button"
                onClick={() => {
                  setPaso(0);
                  setModo("tour");
                }}
                className="border border-rule px-3 py-1.5 text-sm text-ink hover:border-ink transition-colors"
              >
                Hacer el tour
              </button>
              <button
                type="button"
                onClick={cerrar}
                className="border border-ink bg-ink px-4 py-1.5 text-sm font-medium text-paper hover:bg-accent hover:border-accent transition-colors"
              >
                Listo
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
