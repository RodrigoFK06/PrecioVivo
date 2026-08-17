/**
 * Cliente de la API del pipeline (`preciovivo.api`).
 *
 * QUÉ RESUELVE
 * ------------
 * `web/lib/rag.ts` reimplementa en TypeScript el parseo de la pregunta, BM25 y
 * el piso determinista porque el sitio no tenía a quién preguntarle. Es la deuda
 * declarada del proyecto: dos implementaciones de las mismas reglas.
 *
 * `service.recuperar()` existe justamente para eliminarla, y la API la expone en
 * `POST /recuperar`. Este módulo la consume: cuando `PRECIOVIVO_API_URL` está
 * configurada, el sitio usa la MISMA implementación que las evaluaciones, la CLI
 * y el servidor MCP — incluidos el parser de fechas en español completo y la
 * fusión híbrida con vectores, que el port de TypeScript no cubre entero.
 *
 * QUÉ NO RESUELVE, TODAVÍA
 * ------------------------
 * La copia en TypeScript no desaparece: queda de respaldo para cuando la API no
 * está configurada o no responde. El sitio es estático + snapshot y esa
 * propiedad no se sacrifica por esto — si la API se cae, se responde igual.
 * Mientras el respaldo exista, el `contrato de conformidad`
 * (`web/test/conformidad.test.ts`) sigue siendo lo que impide que derive.
 *
 * Al otro lado hay una persona esperando un HTTP, así que el fallo es RÁPIDO:
 * timeout corto y respaldo local, nunca una petición colgada.
 */

const API_URL = process.env.PRECIOVIVO_API_URL;
const API_KEY = process.env.PRECIOVIVO_API_KEY;

/** Timeout del lado del sitio. Ver la nota del módulo: nunca colgar la petición. */
const TIMEOUT_MS = Number(process.env.PRECIOVIVO_API_TIMEOUT_MS ?? "4000");

export type ContextoRemoto = {
  /** El contexto ya serializado para el prompt, tal como lo arma `service`. */
  prompt: string;
  /** Slugs que la API entendió en la pregunta. */
  productos: string[];
  desde: string | null;
  hasta: string | null;
  /** Cuántos fragmentos componen el contexto (garantizados + recuperados). */
  nChunks: number;
};

export function apiConfigurada(): boolean {
  return Boolean(API_URL);
}

type RespuestaRecuperar = {
  interpretacion?: { productos?: unknown; desde?: unknown; hasta?: unknown };
  garantizado?: unknown[];
  recuperado?: unknown[];
  prompt?: unknown;
};

/**
 * Contexto recuperado por el pipeline, o `null` si no se puede usar.
 *
 * Devuelve `null` —en vez de lanzar— ante CUALQUIER problema: sin configurar,
 * timeout, 4xx, 5xx o respuesta con forma inesperada. El llamador cae al
 * respaldo local y la degradación se declara ahí, en un solo sitio.
 */
export async function recuperarViaApi(
  pregunta: string,
  k = 8,
): Promise<ContextoRemoto | null> {
  if (!API_URL) return null;

  const url = new URL("/recuperar", API_URL);
  url.searchParams.set("pregunta", pregunta);
  url.searchParams.set("k", String(k));

  const corte = AbortSignal.timeout(TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      // La API falla CERRADA: sin clave devuelve 503, no datos abiertos. Se
      // manda la cabecera aunque esté vacía para que el 401/403 sea explícito
      // en lugar de parecer un problema de red.
      headers: { "X-API-Key": API_KEY ?? "" },
      signal: corte,
      cache: "no-store",
    });
  } catch {
    return null; // red, DNS o timeout: al respaldo, sin ruido
  }
  if (!resp.ok) return null;

  let datos: RespuestaRecuperar;
  try {
    datos = (await resp.json()) as RespuestaRecuperar;
  } catch {
    return null;
  }

  const prompt = typeof datos.prompt === "string" ? datos.prompt : "";
  if (!prompt.trim()) return null; // sin contexto no aporta nada sobre el respaldo

  const productos = Array.isArray(datos.interpretacion?.productos)
    ? (datos.interpretacion!.productos as unknown[]).filter(
        (s): s is string => typeof s === "string",
      )
    : [];
  const texto = (v: unknown) => (typeof v === "string" ? v : null);

  return {
    prompt,
    productos,
    desde: texto(datos.interpretacion?.desde),
    hasta: texto(datos.interpretacion?.hasta),
    nChunks: (datos.garantizado?.length ?? 0) + (datos.recuperado?.length ?? 0),
  };
}
