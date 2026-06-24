import { describe, it, expect } from "vitest";
import {
  buscarProductos,
  productosToCSV,
  promedioMensualHistorico,
  type Producto,
} from "@/lib/data";

// ---------------------------------------------------------------------------
// Fixtures mínimos (no dependen del snapshot real, así los tests son estables).
// ---------------------------------------------------------------------------

function mkProducto(over: Partial<Producto> & { nombre: string; slug: string }): Producto {
  return {
    nombre: over.nombre,
    slug: over.slug,
    categoria: over.categoria ?? null,
    unidad: over.unidad ?? "kg",
    equiv_kg: over.equiv_kg ?? 1,
    series: over.series ?? [],
    latest: over.latest ?? {
      fecha: "2026-06-23",
      precio_kg: 1,
      precio_ayer_kg: 1,
      var_pct: 0,
      masa_hoy: null,
      masa_7d: null,
      tendencia: "Estable",
    },
    forecast: over.forecast,
  };
}

const papa = mkProducto({
  nombre: "Papa Amarilla",
  slug: "papa-amarilla",
  series: [
    { fecha: "2024-06-01", precio_kg: 2.0, masa_hoy: null, masa_7d: null, tendencia: null },
    { fecha: "2024-06-15", precio_kg: 4.0, masa_hoy: null, masa_7d: null, tendencia: null },
    { fecha: "2025-06-10", precio_kg: 3.0, masa_hoy: null, masa_7d: null, tendencia: null },
    // Punto con precio nulo: debe ignorarse en promedios y CSV de series.
    { fecha: "2025-06-20", precio_kg: null, masa_hoy: null, masa_7d: null, tendencia: null },
    { fecha: "2025-07-01", precio_kg: 9.0, masa_hoy: null, masa_7d: null, tendencia: null },
  ],
  latest: {
    fecha: "2026-06-23",
    precio_kg: 3.5,
    precio_ayer_kg: 3.4,
    var_pct: 2.9,
    masa_hoy: 120,
    masa_7d: 100,
    tendencia: "En Alza",
  },
  forecast: {
    metodo: "gbm",
    horizonte_dias: 1,
    precio_estimado: 3.6,
    intervalo: [3.4, 3.8],
    mae_modelo: 0.06,
    mae_baseline: 0.12,
    n_obs: 492,
  },
});

const ajiMontana = mkProducto({
  nombre: "Aji Montaña",
  slug: "aji-montana",
});

const cebolla = mkProducto({
  nombre: "Cebolla, Cabeza Roja",
  slug: "cebolla-cabeza-roja",
});

const todos = [papa, ajiMontana, cebolla];

describe("buscarProductos", () => {
  it("devuelve TODO con query vacía o solo espacios", () => {
    expect(buscarProductos(todos, "")).toEqual(todos);
    expect(buscarProductos(todos, "   ")).toEqual(todos);
  });

  it("es insensible a mayúsculas", () => {
    const r = buscarProductos(todos, "PAPA");
    expect(r.map((p) => p.slug)).toEqual(["papa-amarilla"]);
  });

  it("es insensible a acentos en la query (montana => Montaña)", () => {
    const r = buscarProductos(todos, "montana");
    expect(r.map((p) => p.slug)).toEqual(["aji-montana"]);
  });

  it("es insensible a acentos en el nombre del producto (montaña query)", () => {
    const r = buscarProductos(todos, "montaña");
    expect(r.map((p) => p.slug)).toEqual(["aji-montana"]);
  });

  it("hace match de subcadena", () => {
    const r = buscarProductos(todos, "cabeza");
    expect(r.map((p) => p.slug)).toEqual(["cebolla-cabeza-roja"]);
  });

  it("devuelve vacío cuando no hay coincidencias", () => {
    expect(buscarProductos(todos, "zzz")).toEqual([]);
  });
});

describe("promedioMensualHistorico", () => {
  it("promedia todos los años del mismo mes, ignorando nulos", () => {
    // junio: 2.0, 4.0 (2024) y 3.0 (2025); el null se ignora. (2+4+3)/3 = 3
    expect(promedioMensualHistorico(papa, 6)).toBeCloseTo(3.0, 10);
  });

  it("promedia un mes con un solo punto", () => {
    // julio: solo 9.0
    expect(promedioMensualHistorico(papa, 7)).toBeCloseTo(9.0, 10);
  });

  it("devuelve null para un mes sin datos válidos", () => {
    expect(promedioMensualHistorico(papa, 1)).toBeNull();
    expect(promedioMensualHistorico(ajiMontana, 6)).toBeNull(); // serie vacía
  });
});

describe("productosToCSV", () => {
  it("modo 'latest' genera cabecera + una fila por producto", () => {
    const csv = productosToCSV([papa], "latest");
    const filas = csv.split("\n");
    expect(filas[0]).toBe(
      "nombre,precio_kg,var_pct,tendencia,ingreso_hoy,forecast",
    );
    // forecast.precio_estimado = 3.6; masa_hoy(ingreso_hoy) = 120
    expect(filas[1]).toBe("Papa Amarilla,3.5,2.9,En Alza,120,3.6");
    expect(filas).toHaveLength(2);
  });

  it("modo 'latest' deja forecast vacío si el producto no tiene forecast", () => {
    const csv = productosToCSV([ajiMontana], "latest");
    const fila = csv.split("\n")[1];
    // termina en coma => columna forecast vacía
    expect(fila.endsWith(",")).toBe(true);
  });

  it("escapa nombres con coma envolviendo en comillas", () => {
    const csv = productosToCSV([cebolla], "latest");
    const fila = csv.split("\n")[1];
    expect(fila.startsWith('"Cebolla, Cabeza Roja",')).toBe(true);
  });

  it("modo 'series' genera una fila por punto y omite precios nulos", () => {
    const csv = productosToCSV([papa], "series");
    const filas = csv.split("\n");
    expect(filas[0]).toBe("fecha,producto,precio_kg");
    // 5 puntos en serie, 1 con precio null => 4 filas de datos + cabecera
    expect(filas).toHaveLength(5);
    expect(filas[1]).toBe("2024-06-01,Papa Amarilla,2");
    // ningún punto con el precio nulo (2025-06-20)
    expect(csv.includes("2025-06-20")).toBe(false);
  });
});
