"""Informe de deriva sobre la serie real del GMML.

    python scripts_deriva.py
    python scripts_deriva.py --ref 2025-06:2025-07 --act 2025-08:2025-08
    python scripts_deriva.py --por-producto

DOS COSAS QUE SE APRENDIERON CORRIENDOLO SOBRE DATOS REALES
------------------------------------------------------------
1. LAS CARACTERISTICAS DE CALENDARIO NO SE PUEDEN VIGILAR ENTRE VENTANAS DE
   CALENDARIO. La primera corrida dio CSI(mes) = 6,10 y CSI(dia_semana) = 0,34
   "significativa". Ninguna de las dos era deriva: la ventana de referencia era
   julio-diciembre y la actual marzo-agosto, asi que el mes difiere por
   DEFINICION, y comparar un mes contra dos cambia la mezcla de dias de la semana
   por el propio calendario. Se calculan igual, porque ver el numero enseña el
   artefacto, pero van marcadas y NO entran en el veredicto.

2. EL PSI AGREGADO SE PIERDE LOS CHOQUES CONCENTRADOS. La semana del 30 de julio
   de 2025 cinco productos cayeron entre 58 % y 61 %. Agosto contra junio-julio
   da PSI = 0,045, "estable". No es un fallo del indice: es lo que significa
   agregar 72 productos en una sola distribucion. Por eso existe --por-producto,
   que es donde ese choque si aparece.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from preciovivo import deriva  # noqa: E402

RUTA_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "data", "preciovivo.db")

# Derivadas de la fecha, no del mercado. Se reportan pero no votan.
DE_CALENDARIO = frozenset({"mes", "dia_semana"})


def _ventana(txt: str) -> tuple[str, str]:
    ini, fin = txt.split(":")
    return ini, fin


def _filas(con: sqlite3.Connection, desde: str, hasta: str):
    return con.execute(
        """select p.fecha, pr.nombre_canonico, p.precio_hoy_kg, p.masa_hoy
             from precios_diarios p
             join mercados m on m.id = p.mercado_id
             join productos pr on pr.id = p.producto_id
            where m.codigo = 'GMML' and p.fecha between ? and ?
              and p.precio_hoy_kg is not null""",
        (desde + "-01", hasta + "-31")).fetchall()


def caracteristicas(filas) -> dict[str, list[float]]:
    precio, masa, dow, mes = [], [], [], []
    for f, _n, pr, ma in filas:
        precio.append(float(pr))
        if ma is not None:
            masa.append(float(ma))
        d = date.fromisoformat(f)
        dow.append(float(d.weekday()))
        mes.append(float(d.month))
    return {"precio_kg": precio, "masa_dia": masa,
            "dia_semana": dow, "mes": mes}


def por_producto(f_ref, f_act, bins: int, minimo: int = 18):
    """PSI del precio, un producto a la vez.

    `minimo` observaciones por lado. 18 y no 40 porque un mes de mercado son ~21
    dias habiles: con 40 no entra ningun producto en una ventana mensual y el
    informe sale vacio en silencio, que es peor que salir con poca precision
    declarada. Por debajo de 18 el indice es ruido y no se reporta.

    Los bins bajan a 5 por la misma razon: 10 cuantiles sobre 20 observaciones
    dan dos datos por bin.
    """
    bins = min(bins, 5)
    ref, act = {}, {}
    for _f, n, pr, _m in f_ref:
        ref.setdefault(n, []).append(float(pr))
    for _f, n, pr, _m in f_act:
        act.setdefault(n, []).append(float(pr))
    fuera = {}
    for n in ref:
        if n in act and len(ref[n]) >= minimo and len(act[n]) >= minimo:
            fuera[n] = deriva.psi(ref[n], act[n], bins)
    return fuera


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deriva PSI/CSI del GMML")
    ap.add_argument("--db", default=RUTA_DEFECTO)
    ap.add_argument("--ref", default="2024-07:2024-12")
    ap.add_argument("--act", default="2026-03:2026-08")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--por-producto", action="store_true",
                    help="PSI por producto: donde aparecen los choques concentrados")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args(argv)

    con = sqlite3.connect(args.db)
    r0, r1 = _ventana(args.ref)
    a0, a1 = _ventana(args.act)
    f_ref, f_act = _filas(con, r0, r1), _filas(con, a0, a1)
    con.close()

    if not f_ref or not f_act:
        print("ERROR: una de las ventanas no tiene datos", file=sys.stderr)
        return 1

    ref, act = caracteristicas(f_ref), caracteristicas(f_act)
    print(f"== Deriva GMML ==\n  referencia {args.ref}  ({len(f_ref):,} obs)"
          f"\n  actual     {args.act}  ({len(f_act):,} obs)\n")

    p = deriva.psi(ref["precio_kg"], act["precio_kg"], args.bins)
    print(f"  PSI (precio publicado)   {p}\n")

    print("  CSI por caracteristica:")
    res = deriva.csi(ref, act, args.bins)
    for nombre, r in sorted(res.items(), key=lambda kv: -kv[1].valor):
        marca = "  [calendario, no vota]" if nombre in DE_CALENDARIO else ""
        print(f"    {nombre:14s} {r}{marca}")
    if DE_CALENDARIO & set(res):
        print("    (las de calendario cambian por como se cortaron las ventanas, "
              "no por el mercado)")

    votan = {k: v for k, v in res.items() if k not in DE_CALENDARIO}
    top = deriva.peor(votan)
    if top:
        nombre, r = top
        print(f"\n  la que mas se movio: {nombre} ({r.valor:.4f}, {r.clase})")

    if args.por_producto:
        pp = por_producto(f_ref, f_act, args.bins)
        # Se ordena entre los INTERPRETABLES. Con ~21 observaciones por lado
        # muchos productos salen degenerados, y como puntuan altisimo coparian
        # el listado con casos que el indice no puede decidir.
        utiles = {n: r for n, r in pp.items() if r.clase != "degenerada"}
        print(f"\n  PSI por producto ({len(pp)} con datos suficientes, "
              f"{len(pp) - len(utiles)} indecidibles), "
              f"los {args.top} que mas se movieron:")
        for n, r in sorted(utiles.items(), key=lambda kv: -kv[1].valor)[:args.top]:
            print(f"    {n:28s} {r.valor:7.4f} [{r.clase}]")
        movidos = sum(1 for r in utiles.values()
                      if r.clase in ("moderada", "significativa"))
        print(f"    {movidos} de {len(utiles)} interpretables por encima de "
              f"{deriva.UMBRAL_MODERADA}, contra un PSI agregado de {p.valor:.4f}")

    return 2 if p.clase == "significativa" else 0


if __name__ == "__main__":
    raise SystemExit(main())
