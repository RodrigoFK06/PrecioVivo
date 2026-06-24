"use client";

import { useCallback } from "react";
import { productosToCSV, type Producto } from "@/lib/data";

type Props = {
  productos: Producto[];
};

/**
 * Botón(es) de descarga de CSV 100% en el cliente.
 *
 * `productosToCSV` es una función pura (sin `fs`), así que generamos el texto
 * en el navegador, lo envolvemos en un Blob y disparamos la descarga con un
 * enlace temporal (`URL.createObjectURL`). Sin backend.
 */
export default function ExportCSV({ productos }: Props) {
  const descargar = useCallback(
    (modo: "latest" | "series") => {
      if (!productos.length) return;

      // BOM UTF-8 para que Excel respete los acentos de los nombres.
      const csv = "﻿" + productosToCSV(productos, modo);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);

      const fecha = new Date().toISOString().slice(0, 10);
      const sufijo = modo === "latest" ? "precios-hoy" : "serie-completa";

      const a = document.createElement("a");
      a.href = url;
      a.download = `precio-vivo-${sufijo}-${fecha}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    [productos],
  );

  const sinDatos = productos.length === 0;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted">Descargar datos:</span>
      <button
        type="button"
        onClick={() => descargar("latest")}
        disabled={sinDatos}
        className="rounded-sm border border-rule bg-card px-3 py-1.5 text-xs font-medium text-ink hover:border-ink disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
      >
        CSV · precios de hoy
      </button>
      <button
        type="button"
        onClick={() => descargar("series")}
        disabled={sinDatos}
        className="rounded-sm border border-rule bg-card px-3 py-1.5 text-xs font-medium text-ink hover:border-ink disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
      >
        CSV · serie completa
      </button>
    </div>
  );
}
