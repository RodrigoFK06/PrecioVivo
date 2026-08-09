import { describe, it, expect } from "vitest";
import {
  mercadosExtra,
  resultadosVerificacion,
  type MercadoExtra,
  type Snapshot,
  type Verificacion,
} from "@/lib/data";

// ---------------------------------------------------------------------------
// Fixtures mínimos (no dependen del snapshot real, así los tests son estables).
// ---------------------------------------------------------------------------

const baseSnap = {
  generatedAt: "2026-07-06T13:00:00Z",
  mercado: "GMML",
  latestFecha: "2026-07-06",
  fechas: ["2026-07-06"],
  productCount: 0,
  attribution: "",
  productos: [],
} satisfies Snapshot;

function mkMercado(over: Partial<MercadoExtra> & { codigo: string }): MercadoExtra {
  return {
    codigo: over.codigo,
    nombre: over.nombre ?? over.codigo,
    latestFecha: over.latestFecha ?? "2026-07-06",
    productos: over.productos ?? [],
  };
}

const verificacion: Verificacion = {
  fuente: "SISAP – MIDAGRI",
  url: "http://sistemas.midagri.gob.pe/…",
  fecha: "2026-07-06",
  umbral_pct: 15,
  contrastados: 3,
  coinciden: 1,
  gmml_disponible: true,
  generado_en: "2026-07-06T13:00:00Z",
  vigente: true,
  resultados: [
    // Sin contraparte: debe ir al FINAL aunque aparezca primero.
    { producto: "AJO CRIOLLO", sisap_kg: 8.0, gmml_kg: null, delta_pct: null, flag: true, detalle: "sin contraparte" },
    { producto: "PAPA BLANCA", sisap_kg: 1.5, gmml_kg: 1.48, delta_pct: 1.4, flag: false, detalle: "coincide" },
    { producto: "CEBOLLA ROJA", sisap_kg: 2.1, gmml_kg: 1.6, delta_pct: 31.3, flag: true, detalle: "delta +31.3% > 15%" },
  ],
};

describe("mercadosExtra", () => {
  it("devuelve [] si el snapshot no trae mercados", () => {
    expect(mercadosExtra(baseSnap)).toEqual([]);
  });

  it("filtra mercados sin ningún precio publicable", () => {
    const s: Snapshot = {
      ...baseSnap,
      mercados: [
        mkMercado({
          codigo: "AVES",
          productos: [{ nombre: "Pollo Vivo", slug: "pollo-vivo", precio_kg: 5.3, var_pct: 3.9 }],
        }),
        mkMercado({
          codigo: "MMF2",
          productos: [{ nombre: "Papaya", slug: "papaya", precio_kg: null, var_pct: null }],
        }),
      ],
    };
    expect(mercadosExtra(s).map((m) => m.codigo)).toEqual(["AVES"]);
  });
});

describe("resultadosVerificacion", () => {
  it("ordena por |delta| desc y deja los sin contraparte al final", () => {
    expect(resultadosVerificacion(verificacion).map((r) => r.producto)).toEqual([
      "CEBOLLA ROJA",
      "PAPA BLANCA",
      "AJO CRIOLLO",
    ]);
  });

  it("no muta el arreglo original", () => {
    const antes = verificacion.resultados.map((r) => r.producto);
    resultadosVerificacion(verificacion);
    expect(verificacion.resultados.map((r) => r.producto)).toEqual(antes);
  });
});
