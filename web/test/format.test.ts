import { describe, it, expect } from "vitest";
import {
  soles,
  pct,
  tons,
  fechaCorta,
  fechaLarga,
} from "@/lib/format";

describe("soles", () => {
  it("formatea con prefijo S/ y 2 decimales por defecto", () => {
    expect(soles(4.5)).toBe("S/ 4.50");
    expect(soles(0)).toBe("S/ 0.00");
    expect(soles(12.345)).toBe("S/ 12.35"); // redondeo a 2 decimales
  });

  it("respeta el número de decimales pedido", () => {
    expect(soles(4.5, 0)).toBe("S/ 5");
    expect(soles(4.5678, 3)).toBe("S/ 4.568");
  });

  it("devuelve em-dash para null/undefined", () => {
    expect(soles(null)).toBe("—");
    expect(soles(undefined)).toBe("—");
  });
});

describe("pct", () => {
  it("añade signo + a valores positivos y un decimal", () => {
    expect(pct(2.5)).toBe("+2.5%");
    expect(pct(0.04)).toBe("+0.0%");
  });

  it("muestra el signo - nativo en negativos", () => {
    expect(pct(-2.5)).toBe("-2.5%");
  });

  it("cero no lleva signo +", () => {
    expect(pct(0)).toBe("0.0%");
  });

  it("devuelve em-dash para null/undefined", () => {
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
  });
});

describe("tons", () => {
  it("añade sufijo t y localiza con es-PE", () => {
    expect(tons(1)).toBe("1 t");
    // es-PE usa coma como separador de miles
    expect(tons(1234)).toBe("1,234 t");
  });

  it("devuelve em-dash para null/undefined", () => {
    expect(tons(null)).toBe("—");
    expect(tons(undefined)).toBe("—");
  });
});

describe("fechaCorta", () => {
  it("formatea 'día mes' abreviado en español sin construir Date (TZ-safe)", () => {
    expect(fechaCorta("2026-06-23")).toBe("23 jun");
    expect(fechaCorta("2024-01-01")).toBe("1 ene");
    expect(fechaCorta("2025-12-09")).toBe("9 dic");
  });
});

describe("fechaLarga", () => {
  it("formatea 'día de mes de año' en español", () => {
    expect(fechaLarga("2026-06-23")).toBe("23 de junio de 2026");
    expect(fechaLarga("2024-01-01")).toBe("1 de enero de 2024");
    expect(fechaLarga("2025-12-09")).toBe("9 de diciembre de 2025");
  });
});
