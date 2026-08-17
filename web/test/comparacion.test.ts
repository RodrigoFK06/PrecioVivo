/**
 * Pruebas de `comparacionHonesta` — la cifra de capacidad del dashboard.
 *
 * POR QUÉ ESTE ARCHIVO EXISTE
 * ---------------------------
 * El hero publicaba "el modelo le gana al baseline en X de N productos", contando
 * productos donde `forecast.metodo === "gbm" && mae_modelo < mae_baseline`. Esa
 * condición no puede fallar: `forecast_producto` (Python) elige `metodo` como el
 * argmin del MAE walk-forward sobre un conjunto que YA INCLUYE al baseline, así
 * que `mae_modelo <= mae_baseline` se cumple en el 100% de los productos por
 * construcción. La cifra medía la selección, no la capacidad.
 *
 * El último test de este archivo es el guard contra que esa métrica vuelva.
 */
import { describe, expect, it } from "vitest";
import { comparacionHonesta, type KillGate } from "@/lib/data";

/** Números reales del snapshot vigente (h=1, 72 productos). */
const KILL_GATE: KillGate = {
  volume_helps: false,
  mae_baseline: 0.2127,
  mae_con_volumen: 0.1654,
  mejora_pct: 0,
  detalle: "",
  n_productos: 65,
  comparacion_modelos: {
    horizontes: [1, 7],
    umbral_gbm: 120,
    gbm_disponible: true,
    veredicto_global: "",
    por_horizonte: {
      "1": {
        mae_baseline: 0.2127,
        mae_ar1: 0.1588,
        mae_volumen: 0.1654,
        mae_gbm: 0.1772,
        n_productos: 72,
        n_productos_gbm: 72,
        ganador: "ar1",
        veredicto: "",
      },
      "7": {
        mae_baseline: 0.5196,
        mae_ar1: 0.4715,
        mae_volumen: 0.4922,
        mae_gbm: 0.4866,
        n_productos: 72,
        n_productos_gbm: 72,
        ganador: "ar1",
        veredicto: "",
      },
    },
  },
};

describe("comparacionHonesta", () => {
  it("ordena las familias de menor a mayor error", () => {
    const c = comparacionHonesta(KILL_GATE, 1)!;
    expect(c.familias.map((f) => f.clave)).toEqual(["ar1", "volumen", "gbm", "baseline"]);
    const maes = c.familias.map((f) => f.mae);
    expect([...maes].sort((a, b) => a - b)).toEqual(maes);
  });

  it("identifica al ganador y reporta que NO es el GBM", () => {
    const c = comparacionHonesta(KILL_GATE, 1)!;
    expect(c.ganador.clave).toBe("ar1");
    expect(c.gbmGana).toBe(false);
    // El hallazgo incómodo que el dashboard ahora publica: una recta de dos
    // parámetros le gana al gradient boosting con 120 árboles.
    expect(c.gbm!.mae).toBeGreaterThan(c.ganador.mae);
  });

  it("calcula la mejora contra el baseline a partir de los MAE, sin literales", () => {
    const c = comparacionHonesta(KILL_GATE, 1)!;
    const esperado = ((0.2127 - 0.1588) / 0.2127) * 100;
    expect(c.ganador.mejoraVsBaselinePct).toBeCloseTo(esperado, 10);
    expect(Math.round(c.ganador.mejoraVsBaselinePct)).toBe(25);
    // El baseline comparado consigo mismo es exactamente 0, no un redondeo.
    expect(c.familias.find((f) => f.clave === "baseline")!.mejoraVsBaselinePct).toBe(0);
  });

  it("soporta el horizonte de 7 días", () => {
    const c = comparacionHonesta(KILL_GATE, 7)!;
    expect(c.horizonte).toBe(7);
    expect(c.ganador.clave).toBe("ar1");
    expect(c.nProductos).toBe(72);
  });

  it("degrada a null si el snapshot no trae la comparación", () => {
    const sinComparacion: KillGate = { ...KILL_GATE, comparacion_modelos: undefined };
    expect(comparacionHonesta(sinComparacion, 1)).toBeNull();
    expect(comparacionHonesta(undefined, 1)).toBeNull();
    expect(comparacionHonesta(KILL_GATE, 99)).toBeNull();
  });

  it("omite las familias que no se pudieron evaluar", () => {
    const sinGbm: KillGate = {
      ...KILL_GATE,
      comparacion_modelos: {
        ...KILL_GATE.comparacion_modelos!,
        por_horizonte: {
          "1": { ...KILL_GATE.comparacion_modelos!.por_horizonte["1"], mae_gbm: null },
        },
      },
    };
    const c = comparacionHonesta(sinGbm, 1)!;
    expect(c.gbm).toBeNull();
    expect(c.gbmGana).toBe(false);
    expect(c.familias.map((f) => f.clave)).not.toContain("gbm");
  });

  it("marcaría gbmGana si el GBM llegara a ganar de verdad", () => {
    const gbmMejor: KillGate = {
      ...KILL_GATE,
      comparacion_modelos: {
        ...KILL_GATE.comparacion_modelos!,
        por_horizonte: {
          "1": { ...KILL_GATE.comparacion_modelos!.por_horizonte["1"], mae_gbm: 0.1 },
        },
      },
    };
    const c = comparacionHonesta(gbmMejor, 1)!;
    expect(c.ganador.clave).toBe("gbm");
    expect(c.gbmGana).toBe(true);
  });

  it("GUARD: la comparación por familia no es tautológica", () => {
    // La métrica vieja se cumplía en el 100% de los productos por construcción.
    // Esta no: el baseline le gana a alguna familia (aquí, a ninguna — pero el
    // punto es que PUEDE), y una familia puede quedar por debajo del baseline.
    const gbmPeorQueBaseline: KillGate = {
      ...KILL_GATE,
      comparacion_modelos: {
        ...KILL_GATE.comparacion_modelos!,
        por_horizonte: {
          "1": { ...KILL_GATE.comparacion_modelos!.por_horizonte["1"], mae_gbm: 0.99 },
        },
      },
    };
    const c = comparacionHonesta(gbmPeorQueBaseline, 1)!;
    const gbm = c.gbm!;
    // Un modelo PUEDE ser peor que el baseline y la función lo reporta como tal,
    // con mejora negativa. Si esto fuera imposible, la métrica no mediría nada.
    expect(gbm.mejoraVsBaselinePct).toBeLessThan(0);
    expect(c.familias[c.familias.length - 1].clave).toBe("gbm");
  });
});
