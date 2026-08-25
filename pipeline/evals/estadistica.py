"""Incertidumbre de las metricas de evaluacion, y lo que NO se puede detectar.

POR QUE EXISTE
--------------
Un numero sin intervalo no se puede comparar con otro numero. Si el recall pasa
de 0,973 a 0,991 sobre 58 casos, eso son DOS casos que cambiaron de lado. Sin
intervalo, "subio" y "es ruido" son la misma frase.

Y hay algo peor que un intervalo ausente: una metrica que no puede moverse. Con
recall@k = 0,991 quedan 0,9 puntos de margen hasta el techo. Buscar ahi una
mejora de 5 puntos es imposible por aritmetica, no por falta de talento. Este
modulo lo calcula y lo dice en vez de dejar que el numero perfecto pase por buena
noticia.

LAS TRES COSAS QUE CALCULA
---------------------------
1. INTERVALO DE WILSON sobre una proporcion. No el normal (Wald): con p cerca de
   0 o de 1 -que es justo donde vive este proyecto- Wald da intervalos que se
   salen de [0,1] y colapsan a cero de ancho cuando p=1. Wilson no.

2. MDE PAREADO (minimum detectable effect). Dos configuraciones se comparan sobre
   LOS MISMOS casos, asi que solo cuentan los discordantes: los casos donde una
   acierta y la otra no. Es el diseno de McNemar, y no es un atajo barato sino el
   correcto -- comparar dos muestras independientes cuando en realidad son las
   mismas preguntas tira potencia a la basura.

       Var(p1 - p2) = d / n        con d = tasa de discordancia
       MDE = (z_alfa/2 + z_beta) * raiz(d / n)

3. MARGEN AL TECHO. Cuanto puede subir la metrica antes de chocar con 1,0. Si el
   margen es menor que el MDE, la metrica NO PUEDE demostrar la mejora que se le
   pide, y eso hay que decirlo.

LIMITE CONOCIDO Y ACEPTADO
---------------------------
El MDE depende de la tasa de discordancia, que no se conoce hasta comparar dos
configuraciones de verdad. Por eso se reporta una TABLA sobre varias tasas
plausibles en vez de un solo numero con falsa precision. Cuando haya una ablacion
real, la tasa se mide y el numero deja de ser un supuesto.
"""
from __future__ import annotations

import math

# z de dos colas al 95 % y z de una cola para potencia del 80 %. Escritos con sus
# valores y no importados de scipy: el paquete de evaluacion no arrastra scipy
# por dos constantes.
Z_ALFA_2 = 1.959964
Z_BETA_80 = 0.841621


def wilson(exitos: float, n: int, z: float = Z_ALFA_2) -> tuple[float, float]:
    """Intervalo de Wilson al 95 % para una proporcion.

    `exitos` es float y no int a proposito: aqui el recall por caso puede ser
    fraccionario (2 de 3 predicados satisfechos), asi que la suma de recalls no
    es un conteo. Es una aproximacion -- Wilson esta definido sobre conteos --
    y se declara: el intervalo que sale es correcto en orden de magnitud, no
    exacto. Para las proporciones que SI son conteos (casos perfectos,
    violaciones) es exacto.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = max(0.0, min(1.0, exitos / n))
    z2 = z * z
    denom = 1 + z2 / n
    centro = (p + z2 / (2 * n)) / denom
    medio = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, centro - medio), min(1.0, centro + medio))


def mde_pareado(n: int, discordancia: float,
                z_alfa: float = Z_ALFA_2, z_beta: float = Z_BETA_80) -> float:
    """Diferencia minima detectable entre dos configuraciones sobre los MISMOS casos.

    Devuelve la diferencia absoluta de proporciones (0-1) que este tamano de
    muestra puede distinguir del ruido con alfa 0,05 y potencia 0,80.
    """
    if n <= 0 or discordancia <= 0:
        return 1.0
    return min(1.0, (z_alfa + z_beta) * math.sqrt(discordancia / n))


def margen_al_techo(p: float) -> float:
    """Cuanto puede subir la metrica antes de chocar con 1,0."""
    return max(0.0, 1.0 - p)


def diagnostico(p: float, n: int, discordancias=(0.05, 0.10, 0.20)) -> dict:
    """Todo junto: intervalo, MDE por tasa de discordancia, y si la metrica sirve.

    `sirve` es False cuando el margen al techo es menor que el MDE mas optimista.
    Significa: aunque la mejora exista y sea real, esta metrica sobre esta
    muestra no puede ensenarla. No es un fallo del sistema medido; es un fallo
    del instrumento.
    """
    lo, hi = wilson(p * n, n)
    mdes = {d: mde_pareado(n, d) for d in discordancias}
    margen = margen_al_techo(p)
    mejor_mde = min(mdes.values()) if mdes else 1.0
    return {
        "p": p, "n": n,
        "ic95": (lo, hi),
        "ancho_ic": hi - lo,
        "mde": mdes,
        "margen_al_techo": margen,
        "sirve": margen >= mejor_mde,
    }


def linea_mde(nombre: str, d: dict) -> list[str]:
    """El diagnostico en lineas de texto, listo para el reporte."""
    lo, hi = d["ic95"]
    out = [f"  {nombre:<22}{d['p']:.3f}   IC95 [{lo:.3f}, {hi:.3f}]  "
           f"(n={d['n']}, ancho {d['ancho_ic']:.3f})"]
    tabla = "  ".join(f"d={k:.0%}: {v:.3f}" for k, v in sorted(d["mde"].items()))
    out.append(f"    MDE pareado          {tabla}")
    if not d["sirve"]:
        out.append(
            f"    SATURADA: quedan {d['margen_al_techo']:.3f} de margen al techo y "
            f"el MDE mas optimista es {min(d['mde'].values()):.3f}.")
        out.append(
            "    Esta metrica NO puede demostrar una mejora aunque exista. "
            "Usa una que tenga recorrido.")
    return out
