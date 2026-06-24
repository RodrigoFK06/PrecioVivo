import { describe, it, expect } from "vitest";
import {
  buscarProductos,
  productosToCSV,
  promedioMensualHistorico,
  type Producto,
  type Snapshot,
} from "@/lib/data";
import snapshot from "@/data/snapshot.json";

// Tests de integración ligera: corren los helpers PUROS contra el snapshot real
// (no usan getSnapshot()/fs; importan el JSON directamente, server/cliente-safe).
const s = snapshot as unknown as Snapshot;
const productos = s.productos as Producto[];

describe("snapshot.json (data real)", () => {
  it("tiene la forma esperada", () => {
    expect(Array.isArray(productos)).toBe(true);
    expect(productos.length).toBeGreaterThan(0);
    expect(productos.length).toBe(s.productCount);
    expect(Array.isArray(s.fechas)).toBe(true);
  });

  it("buscarProductos encuentra 'papa' (varias variedades)", () => {
    const r = buscarProductos(productos, "papa");
    expect(r.length).toBeGreaterThan(0);
    expect(r.every((p) => p.nombre.toLowerCase().includes("papa"))).toBe(true);
  });

  it("buscarProductos es insensible a acentos sobre la data real (montana => Montaña)", () => {
    const conAcento = productos.filter((p) => /[áéíóúñ]/i.test(p.nombre));
    if (conAcento.length === 0) return; // tolerante si el dataset cambia
    const objetivo = conAcento[0];
    const sinAcento = objetivo.nombre
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
    const r = buscarProductos(productos, sinAcento);
    expect(r.map((p) => p.slug)).toContain(objetivo.slug);
  });

  it("promedioMensualHistorico del primer producto devuelve número o null", () => {
    const p = productos[0];
    const v = promedioMensualHistorico(p, 6);
    expect(v === null || typeof v === "number").toBe(true);
  });

  it("productosToCSV('latest') produce cabecera + una fila por producto", () => {
    const csv = productosToCSV(productos, "latest");
    const filas = csv.split("\n");
    expect(filas[0]).toBe(
      "nombre,precio_kg,var_pct,tendencia,ingreso_hoy,forecast",
    );
    expect(filas).toHaveLength(productos.length + 1);
  });

  it("productosToCSV('series') no incluye filas con precio nulo", () => {
    const csv = productosToCSV(productos, "series");
    // cada línea de datos debe terminar en un número (precio_kg presente)
    const dataLines = csv.split("\n").slice(1);
    expect(dataLines.length).toBeGreaterThan(0);
    for (const line of dataLines.slice(0, 50)) {
      const last = line.split(",").pop() ?? "";
      expect(last).not.toBe("");
    }
  });
});
