/**
 * Cliente de la API del pipeline.
 *
 * Lo que se prueba es sobre todo el CONTRATO DE FALLO. Este módulo es el único
 * del sitio que depende de un servicio remoto en caliente, y su regla es que
 * NUNCA lanza: ante cualquier problema devuelve `null` y el llamador cae al
 * respaldo local. Si esa regla se rompe, una API caída deja de degradar y pasa
 * a tumbar `/api/consulta`, que es exactamente lo contrario de para lo que está.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const URL_API = "https://api.preciovivo.test";

function respuestaOk(extra: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      pregunta: "¿por qué subió la papa?",
      interpretacion: { productos: ["papa-blanca"], desde: "2026-08-10", hasta: "2026-08-14" },
      garantizado: [{ id: "a" }, { id: "b" }],
      recuperado: [{ id: "c" }],
      prompt: "=== CONTEXTO GARANTIZADO ===\nPapa Blanca · semana 2026-W32.",
      ...extra,
    }),
  };
}

async function importar({ url, key }: { url?: string; key?: string } = {}) {
  vi.resetModules();
  if (url === undefined) delete process.env.PRECIOVIVO_API_URL;
  else process.env.PRECIOVIVO_API_URL = url;
  if (key === undefined) delete process.env.PRECIOVIVO_API_KEY;
  else process.env.PRECIOVIVO_API_KEY = key;
  return import("@/lib/api");
}

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.PRECIOVIVO_API_URL;
  delete process.env.PRECIOVIVO_API_KEY;
});

describe("recuperarViaApi", () => {
  it("sin PRECIOVIVO_API_URL no llama a nadie", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { apiConfigurada, recuperarViaApi } = await importar();

    expect(apiConfigurada()).toBe(false);
    expect(await recuperarViaApi("papa")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("devuelve el contexto del pipeline y manda la clave", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respuestaOk());
    vi.stubGlobal("fetch", fetchMock);
    const { recuperarViaApi } = await importar({ url: URL_API, key: "clave" });

    const ctx = await recuperarViaApi("¿por qué subió la papa?", 8);
    expect(ctx).not.toBeNull();
    expect(ctx!.prompt).toContain("CONTEXTO GARANTIZADO");
    expect(ctx!.productos).toEqual(["papa-blanca"]);
    expect(ctx!.desde).toBe("2026-08-10");
    expect(ctx!.nChunks).toBe(3);

    const [url, opciones] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/recuperar");
    expect(String(url)).toContain("pregunta=");
    expect(opciones.headers["X-API-Key"]).toBe("clave");
  });

  it("manda la cabecera vacía si no hay clave, para que el 401 sea explícito", async () => {
    // La API falla CERRADA: sin claves devuelve 503. Omitir la cabecera haría
    // que el rechazo pareciera un problema de red en vez de configuración.
    const fetchMock = vi.fn().mockResolvedValue(respuestaOk());
    vi.stubGlobal("fetch", fetchMock);
    const { recuperarViaApi } = await importar({ url: URL_API });

    await recuperarViaApi("papa");
    expect(fetchMock.mock.calls[0][1].headers["X-API-Key"]).toBe("");
  });

  it.each([
    ["503 (API sin claves configuradas)", { ok: false, status: 503, json: async () => ({}) }],
    ["403 (clave inválida)", { ok: false, status: 403, json: async () => ({}) }],
    ["500 (fallo del servidor)", { ok: false, status: 500, json: async () => ({}) }],
  ])("devuelve null ante %s", async (_caso, resp) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(resp));
    const { recuperarViaApi } = await importar({ url: URL_API, key: "k" });
    expect(await recuperarViaApi("papa")).toBeNull();
  });

  it("un fallo de red o un timeout NO lanza: cae al respaldo", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const { recuperarViaApi } = await importar({ url: URL_API, key: "k" });
    await expect(recuperarViaApi("papa")).resolves.toBeNull();
  });

  it("una respuesta con forma inesperada no revienta", async () => {
    for (const cuerpo of [
      { json: async () => ({ prompt: 123 }) },              // prompt no es string
      { json: async () => ({}) },                            // sin prompt
      { json: async () => ({ prompt: "   " }) },             // prompt vacío
      { json: async () => { throw new Error("no es json"); } },
    ]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, ...cuerpo }));
      const { recuperarViaApi } = await importar({ url: URL_API, key: "k" });
      expect(await recuperarViaApi("papa")).toBeNull();
    }
  });

  it("tolera una interpretación incompleta sin perder el contexto", async () => {
    // El prompt es lo que de verdad se necesita; los metadatos son auxiliares.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ prompt: "contexto útil" }),
    }));
    const { recuperarViaApi } = await importar({ url: URL_API, key: "k" });
    const ctx = await recuperarViaApi("papa");
    expect(ctx!.prompt).toBe("contexto útil");
    expect(ctx!.productos).toEqual([]);
    expect(ctx!.desde).toBeNull();
    expect(ctx!.nChunks).toBe(0);
  });
});
