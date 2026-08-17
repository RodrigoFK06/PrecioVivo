/**
 * Pruebas del embebido de la CONSULTA en el sitio.
 *
 * POR QUÉ IMPORTA
 * ---------------
 * El índice viaja en el deploy, pero la pregunta se embebe en cada request. Ese
 * es el único punto del sitio que depende de un proveedor externo en caliente, y
 * los free tier tienen cuotas por minuto muy bajas (Gemini: 100 embeddings/min).
 * Sin reintento, una ráfaga de tráfico convierte cada consulta en una
 * degradación silenciosa: `recuperar` captura el error, devuelve el piso
 * determinista y la respuesta sale igual de bonita, con menos evidencia detrás.
 *
 * La disciplina aquí es la CONTRARIA a la del backfill: allí esperar 34 s es lo
 * correcto; aquí hay una persona esperando un HTTP, así que se reintenta solo si
 * la espera es corta y, si no, se degrada declarándolo.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const FIRMA = "api:text-embedding-3-small:256";

function vectorOk(dims = 256) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => ({ data: [{ embedding: Array(dims).fill(0.1) }] }),
  };
}

function tooManyRequests(retryAfter?: string) {
  return {
    ok: false,
    status: 429,
    headers: { get: (k: string) => (k === "retry-after" ? (retryAfter ?? null) : null) },
    json: async () => ({}),
  };
}

async function importarLimpio() {
  vi.resetModules();
  process.env.EMBED_API_KEY = "clave-de-prueba";
  process.env.EMBED_MODEL = "text-embedding-3-small";
  process.env.EMBED_DIMS = "256";
  return import("@/lib/rag");
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("embedConsulta", () => {
  it("reintenta una vez cuando el proveedor pide una espera CORTA", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(tooManyRequests("1"))
      .mockResolvedValueOnce(vectorOk());
    vi.stubGlobal("fetch", fetchMock);

    const { embedConsulta } = await importarLimpio();
    const v = await embedConsulta("¿a cuánto está la papa?", FIRMA);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(v).toHaveLength(256);
  });

  it("NO espera cuando el proveedor pide una espera larga: degrada", async () => {
    // 34 s es lo que devuelve Gemini al agotar la cuota del free tier. Esperar
    // eso dejaría la petición del usuario colgada; es preferible responder con
    // el piso determinista y DECIR que se degradó.
    const fetchMock = vi.fn().mockResolvedValue(tooManyRequests("34"));
    vi.stubGlobal("fetch", fetchMock);

    const { embedConsulta } = await importarLimpio();
    await expect(embedConsulta("papa", FIRMA)).rejects.toThrow("429");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("no reintenta errores que no son de cuota", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: { get: () => null },
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { embedConsulta } = await importarLimpio();
    await expect(embedConsulta("papa", FIRMA)).rejects.toThrow("401");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("se niega a consultar si la firma del índice no es la del entorno", async () => {
    const fetchMock = vi.fn().mockResolvedValue(vectorOk());
    vi.stubGlobal("fetch", fetchMock);

    const { embedConsulta } = await importarLimpio();
    // Comparar espacios vectoriales distintos devuelve ruido con total
    // confianza: hay que fallar ANTES de gastar la llamada.
    await expect(
      embedConsulta("papa", "local:minishlab/potion-multilingual-128M:256"),
    ).rejects.toThrow(/se construyó con/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("L2-normaliza el vector devuelto", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(vectorOk()));
    const { embedConsulta } = await importarLimpio();
    const v = await embedConsulta("papa", FIRMA);
    const norma = Math.sqrt([...v].reduce((a, x) => a + x * x, 0));
    // El corpus va normalizado, así que el coseno es un producto punto: la
    // consulta tiene que cumplir lo mismo o los scores no son comparables.
    expect(norma).toBeCloseTo(1, 5);
  });
});
