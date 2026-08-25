"""Extrae el PERFIL ESTADISTICO de la serie real del GMML.

    python lakehouse/perfil.py                 # escribe lakehouse/perfil_gmml.json

POR QUE UN PERFIL Y NO LOS DATOS
---------------------------------
Para la prueba de carga hacen falta gigabytes dentro de Databricks. Subirlos
desde Lima no es viable, y Free Edition ademas restringe la salida a internet
hasta que verificas la cuenta.

La salida es que lo que viaja son unos POCOS KB -- este perfil -- y los
gigabytes se generan con Spark ya dentro, con `range()`. El perfil describe la
FORMA de la serie real; el generador la expande a la escala que se le pida.

POR QUE BOOTSTRAP Y NO RUIDO GAUSSIANO
---------------------------------------
Medido sobre 37.988 retornos diarios reales:

    desviacion   0,0822
    curtosis     24,2      (una gaussiana da 3,0)
    |ret| > 3s   2,03 %    (una gaussiana da 0,27 %)

Colas gordas: siete veces mas eventos extremos de los que predice una normal. Y
eso no es un detalle estetico en una prueba de CARGA. Datos gaussianos comprimen
mejor y se reparten mas parejo entre particiones, asi que harian quedar a Spark
mejor de lo que le corresponde. El generador tiene que heredar la forma real o la
medicion no vale.

Se guarda la distribucion empirica como su funcion inversa muestreada en una
REJILLA NO UNIFORME: densa en las colas, normal en el centro. El generador saca
u ~ U(0,1) e interpola sobre esa rejilla.

Que la rejilla sea no uniforme no es refinamiento: es la diferencia entre que
funcione y que no. Con 101 cuantiles equiespaciados, el primer tramo va del
minimo (-1,33) al percentil 1 (-0,24), y la interpolacion reparte ese 1 % de masa
UNIFORMEMENTE por todo el rango, cuando en realidad casi toda esta pegada a
-0,24. Medido sobre 200.000 muestras:

    rejilla                 puntos   desviacion        curtosis
    101 uniformes              101   +66,5 %           +95,0 %
    1000 uniformes            1001    +5,8 %           +70,6 %
    101 + colas densas         111    +0,1 %           -11,6 %

Once puntos bien colocados baten a novecientos mal colocados, porque el problema
era resolucion en las COLAS, no resolucion global.

QUE NO ES
---------
El perfil no lleva ningun precio real ni ninguna fecha real: solo agregados de
forma. Los datos que salgan del generador son SINTETICOS y no sostienen ninguna
afirmacion sobre el mercado peruano. Sirven para medir rendimiento y nada mas.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

AQUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(AQUI, "..", "data", "preciovivo.db")
SALIDA = os.path.join(AQUI, "perfil_gmml.json")
# Rejilla de probabilidades: percentiles enteros mas cinco puntos extra pegados a
# cada extremo. 111 en total.
_COLAS = (0.0, 0.0001, 0.0005, 0.001, 0.0025, 0.005)
REJILLA = sorted(set(list(_COLAS)
                     + [k / 100 for k in range(1, 100)]
                     + [1 - x for x in _COLAS]))


def _cuantiles(datos: list[float], rejilla: list[float] = REJILLA) -> list[float]:
    """La funcion inversa de la distribucion, muestreada en `rejilla`."""
    orden = sorted(datos)
    if not orden:
        return []
    return [round(orden[int(round(p * (len(orden) - 1)))], 6) for p in rejilla]


def construir(db: str) -> dict:
    con = sqlite3.connect(db)

    series: dict[str, list[tuple[str, float, float | None]]] = defaultdict(list)
    for nombre, fecha, precio, masa in con.execute(
            """select pr.nombre_canonico, p.fecha, p.precio_hoy_kg, p.masa_hoy
                 from precios_diarios p
                 join productos pr on pr.id = p.producto_id
                 join mercados m on m.id = p.mercado_id
                where m.codigo = 'GMML' and p.precio_hoy_kg is not null
                order by 1, 2"""):
        series[nombre].append((fecha, float(precio), masa))

    unidades = {n: (u, e) for n, u, e in con.execute(
        "select nombre_canonico, unidad_default, equiv_kg from productos")}

    retornos: list[float] = []
    productos = []
    por_mes: dict[int, list[float]] = defaultdict(list)
    dias_semana: dict[int, int] = defaultdict(int)
    masas: list[float] = []

    for nombre, filas in series.items():
        precios = [p for _f, p, _m in filas]
        propios = []
        for (_, a, _), (_, b, _) in zip(filas, filas[1:], strict=False):
            if a > 0:
                r = math.log(b / a)
                retornos.append(r)
                propios.append(r)
        for f, p, m in filas:
            anio, mes, dia = (int(x) for x in f.split("-"))
            por_mes[mes].append(p)
            # dia de la semana sin importar datetime: algoritmo de Sakamoto
            dias_semana[_dow(anio, mes, dia)] += 1
            if m is not None:
                masas.append(float(m))
        u, e = unidades.get(nombre, ("Kilogramo", 1.0))
        productos.append({
            "unidad": u or "Kilogramo",
            "equiv_kg": e or 1.0,
            "precio_inicial": round(statistics.median(precios), 4),
            "vol_diaria": round(statistics.pstdev(propios), 5) if len(propios) > 1 else 0.05,
            "n_obs": len(filas),
        })
    con.close()

    global_media = statistics.mean([p for ps in por_mes.values() for p in ps])
    estacionalidad = {str(m): round(statistics.mean(v) / global_media, 4)
                      for m, v in sorted(por_mes.items())}

    return {
        "_aviso": ("Perfil de FORMA de la serie real del GMML. No contiene precios "
                   "ni fechas reales. Los datos generados a partir de el son "
                   "SINTETICOS y solo sirven para medir rendimiento."),
        "n_productos_reales": len(productos),
        "n_retornos": len(retornos),
        "rejilla": REJILLA,
        "retornos": {
            "media": round(statistics.mean(retornos), 6),
            "desv": round(statistics.pstdev(retornos), 6),
            "curtosis": round(_curtosis(retornos), 2),
            "cuantiles": _cuantiles(retornos),
        },
        "masa": {
            "mediana": round(statistics.median(masas), 2) if masas else 0.0,
            "cuantiles": _cuantiles(masas),
            "fraccion_nula": round(1 - len(masas) / max(sum(
                len(v) for v in series.values()), 1), 4),
        },
        "estacionalidad_mensual": estacionalidad,
        "dias_semana": {str(k): v for k, v in sorted(dias_semana.items())},
        "productos": productos,
    }


def _dow(y: int, m: int, d: int) -> int:
    """Dia de la semana 0=lunes. Sakamoto, para no importar datetime aqui."""
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if m < 3:
        y -= 1
    return ((y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7 + 6) % 7


def _curtosis(xs: list[float]) -> float:
    m, s = statistics.mean(xs), statistics.pstdev(xs)
    if s == 0:
        return 0.0
    return sum(((x - m) / s) ** 4 for x in xs) / len(xs)


def main(argv=None) -> int:
    db = (argv or sys.argv[1:] or [DB])[0]
    if not os.path.exists(db):
        print(f"ERROR: no existe {db}", file=sys.stderr)
        return 1
    perfil = construir(db)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=1)
    tam = os.path.getsize(SALIDA) / 1024
    print(f"perfil escrito: {SALIDA}  ({tam:.1f} KB)")
    print(f"  productos reales   {perfil['n_productos_reales']}")
    print(f"  retornos           {perfil['n_retornos']:,}")
    print(f"  desv / curtosis    {perfil['retornos']['desv']:.4f} / "
          f"{perfil['retornos']['curtosis']}")
    print(f"  masa nula          {perfil['masa']['fraccion_nula']:.1%}")
    est = perfil["estacionalidad_mensual"]
    alto = max(est, key=lambda k: est[k])
    bajo = min(est, key=lambda k: est[k])
    print(f"  estacionalidad     mes {alto} x{est[alto]}  ·  mes {bajo} x{est[bajo]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
