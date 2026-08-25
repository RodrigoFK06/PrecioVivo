"""Generador sintetico a partir del perfil, y su verificacion.

    python lakehouse/generar.py --productos 200 --dias 500 --verificar
    python lakehouse/generar.py --productos 5000 --dias 2000 --salida out.parquet

DE QUE SIRVE UNA VERSION LOCAL SI LOS GIGABYTES SE GENERAN EN SPARK
--------------------------------------------------------------------
De dos cosas:

1. VERIFICAR QUE EL GENERADOR HEREDA LA FORMA REAL. Un generador que produce una
   serie demasiado limpia invalida la prueba de carga entera, y eso no se ve
   mirando el codigo: hay que generar, medir y comparar contra el objetivo. Es
   barato hacerlo con 200 productos en local y caro descubrirlo con 10 GB dentro
   de Databricks.

2. DAR LA LINEA BASE. "Spark tardo 30 segundos" no significa nada sin el numero
   de al lado. La comparacion util es contra lo que ya existe -- SQLite y pandas
   en una maquina -- y contra el punto donde eso deja de servir.

RIESGO DECLARADO: DOS IMPLEMENTACIONES
---------------------------------------
El generador de Spark hara la misma aritmetica en otro lenguaje, asi que pueden
divergir. Es el mismo problema que `retrieval.py` contra `rag.ts` en este
repositorio, y se ataja igual: el notebook verifica su propia salida contra los
MISMOS objetivos del perfil, y si se separa, falla.

EL METODO
---------
Camino aleatorio multiplicativo sobre el precio inicial de cada producto:

    precio[t] = precio[t-1] * exp(r)      con r ~ distribucion empirica

`r` NO sale de una normal. Se saca u ~ U(0,1) y se interpola en la rejilla NO
uniforme del perfil, que es la funcion inversa de la distribucion real muestreada
densa en las colas. Asi la curtosis de 24 sobrevive, y con ella las colas que
hacen realista la prueba de carga.

LA ESTACIONALIDAD NO SE APLICA ENCIMA, y es deliberado: los retornos empiricos
que alimentan el camino YA la contienen, porque salen de la serie real completa.
Multiplicar por un factor mensual encima la contaria dos veces y meteria un salto
artificial en cada cambio de mes. El perfil la guarda igualmente, como
documentacion de la forma medida.

Si va un tope de cordura: los precios se recortan a [0,2 · inicial, 8 · inicial]
para que un camino aleatorio largo no se escape a valores absurdos. El recorte se
cuenta y se reporta, porque muerde la cola y eso hay que saberlo.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from datetime import date, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(AQUI, "perfil_gmml.json")

# Un camino multiplicativo sin freno acaba en 0 o en el infinito. Los limites se
# fijan sobre el precio inicial de CADA producto, no globales.
PISO_REL, TECHO_REL = 0.2, 8.0


def cargar_perfil(ruta: str = PERFIL) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _muestrear(cuantiles: list[float], rejilla: list[float], u: float) -> float:
    """Inversa de la distribucion empirica, interpolada sobre rejilla NO uniforme.

    La rejilla es densa en las colas. Interpolar como si fuera uniforme inflo la
    desviacion un 66 % y la curtosis un 95 % en la primera version, porque
    repartia la masa del percentil extremo por todo su rango.
    """
    if not cuantiles:
        return 0.0
    i = 0
    while i < len(rejilla) - 2 and u > rejilla[i + 1]:
        i += 1
    ancho = rejilla[i + 1] - rejilla[i]
    frac = 0.0 if ancho == 0 else (u - rejilla[i]) / ancho
    return cuantiles[i] * (1 - frac) + cuantiles[i + 1] * frac


def generar(perfil: dict, n_productos: int, n_dias: int, semilla: int = 7):
    """Genera filas sinteticas. Devuelve (filas, diagnostico).

    Cada fila: (fecha, producto_id, precio_kg, masa, unidad, equiv_kg).
    """
    rnd = random.Random(semilla)
    rejilla = perfil["rejilla"]
    qr = perfil["retornos"]["cuantiles"]
    qm = perfil["masa"]["cuantiles"]
    frac_nula = perfil["masa"]["fraccion_nula"]
    base = perfil["productos"]

    # Se reciclan los productos reales tantas veces como haga falta: la variedad
    # de formas viene del perfil, la ESCALA de repetirlas. Es lo que se quiere
    # para una prueba de carga y se declara para que nadie lo lea como variedad
    # real de catalogo.
    plantillas = [base[i % len(base)] for i in range(n_productos)]
    precios = [p["precio_inicial"] * rnd.uniform(0.7, 1.4) for p in plantillas]
    iniciales = list(precios)

    inicio = date(2019, 11, 4)
    fechas = []
    d = inicio
    while len(fechas) < n_dias:
        if d.weekday() < 6:          # el GMML no publica domingos
            fechas.append(d)
        d += timedelta(days=1)

    filas, recortes = [], 0
    for f in fechas:
        for i, plant in enumerate(plantillas):
            r = _muestrear(qr, rejilla, rnd.random())
            nuevo = precios[i] * math.exp(r)
            lo, hi = iniciales[i] * PISO_REL, iniciales[i] * TECHO_REL
            if nuevo < lo or nuevo > hi:
                recortes += 1
                nuevo = min(max(nuevo, lo), hi)
            precios[i] = nuevo
            masa = (None if rnd.random() < frac_nula
                    else _muestrear(qm, rejilla, rnd.random()))
            filas.append((f.isoformat(), i, round(nuevo, 4),
                          None if masa is None else round(masa, 2),
                          plant["unidad"], plant["equiv_kg"]))
    return filas, {"recortes": recortes, "n_filas": len(filas),
                   "pct_recortes": round(100 * recortes / max(len(filas), 1), 3)}


def verificar(perfil: dict, filas) -> dict:
    """Compara la forma de lo generado contra los objetivos del perfil.

    Es la unica parte que decide si el generador sirve. Sin esto, "genere 10 GB"
    solo dice que se lleno un disco.
    """
    por_prod = {}
    for _f, pid, precio, _m, _u, _e in filas:
        por_prod.setdefault(pid, []).append(precio)
    ret = []
    for vs in por_prod.values():
        for a, b in zip(vs, vs[1:], strict=False):
            if a > 0:
                ret.append(math.log(b / a))
    if len(ret) < 2:
        return {}
    m, s = statistics.mean(ret), statistics.pstdev(ret)
    k = sum(((x - m) / s) ** 4 for x in ret) / len(ret) if s else 0.0
    fuera3 = 100 * sum(1 for x in ret if abs(x - m) > 3 * s) / len(ret)
    obj = perfil["retornos"]
    return {
        "desv":      {"generado": round(s, 4), "objetivo": obj["desv"]},
        "curtosis":  {"generado": round(k, 1), "objetivo": obj["curtosis"]},
        "pct_3desv": {"generado": round(fuera3, 2), "objetivo": 2.03},
        "n_retornos": len(ret),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generador sintetico GMML")
    ap.add_argument("--perfil", default=PERFIL)
    ap.add_argument("--productos", type=int, default=200)
    ap.add_argument("--dias", type=int, default=500)
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--verificar", action="store_true")
    ap.add_argument("--salida", help="CSV de salida; sin esto no escribe nada")
    args = ap.parse_args(argv)

    perfil = cargar_perfil(args.perfil)
    filas, diag = generar(perfil, args.productos, args.dias, args.semilla)
    print(f"generadas {diag['n_filas']:,} filas  "
          f"({args.productos} productos x {args.dias} dias)")
    print(f"  recortes por tope: {diag['recortes']:,} ({diag['pct_recortes']} %)")

    if args.verificar:
        v = verificar(perfil, filas)
        print(f"\n  forma generada contra el objetivo del perfil "
              f"({v['n_retornos']:,} retornos):")
        ok = True
        for nombre, d in (("desviacion", v["desv"]), ("curtosis", v["curtosis"]),
                          ("% mas de 3 desv", v["pct_3desv"])):
            g, o = d["generado"], d["objetivo"]
            desv_rel = abs(g - o) / max(abs(o), 1e-9)
            marca = "ok" if desv_rel < 0.20 else "SE APARTA"
            if desv_rel >= 0.20:
                ok = False
            print(f"    {nombre:16s} {g:>8}  objetivo {o:>8}   {marca}")
        if not ok:
            print("\n  El generador NO hereda la forma real. Una prueba de carga "
                  "sobre esto mediria otra cosa.", file=sys.stderr)
            return 2

    if args.salida:
        with open(args.salida, "w", encoding="utf-8", newline="") as f:
            f.write("fecha,producto_id,precio_kg,masa,unidad,equiv_kg\n")
            for fila in filas:
                f.write(",".join("" if x is None else str(x) for x in fila) + "\n")
        print(f"\n  escrito {args.salida}  "
              f"({os.path.getsize(args.salida) / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
