"""Evaluación de GENERACIÓN: ¿la respuesta se ciñe a lo que se recuperó?

QUÉ MIDE Y POR QUÉ NO ES LO MISMO QUE `run_retrieval.py`
--------------------------------------------------------
El arnés de recuperación mide si el chunk correcto llegó al prompt. Eso deja
fuera la mitad del problema: un sistema puede recuperar el chunk perfecto y aun
así responder una cifra inventada, y el usuario no tiene forma de saberlo. Las
dos preguntas son independientes y hay que medirlas por separado.

Aquí se miden cuatro cosas, todas contra la REGLA ABSOLUTA nº1 del prompt de
`ai.answer_with_context`: «Usa EXCLUSIVAMENTE los datos del contexto. Nunca
inventes cifras.»

  1. CIFRAS NO RESPALDADAS. Cada número que la respuesta afirma tiene que estar
     en el contexto recuperado, o derivarse de él con aritmética simple.
  2. ABSTENCIÓN. Cuando se pregunta por algo que no está en el catálogo, la
     respuesta no puede afirmar un precio. Ni el de un producto parecido.
  3. CONTENIDO OBLIGADO Y PROHIBIDO. Para las preguntas adversariales hay
     afirmaciones que la respuesta TIENE que hacer y otras que no puede hacer.
     Ver `debe_afirmar` / `no_debe_afirmar` más abajo.
  4. COSTE por consulta, en tokens y en dólares.

POR QUÉ SIN JUEZ LLM, Y QUÉ SE PIERDE CON ESO
----------------------------------------------
Lo habitual para «faithfulness» es pedirle a otro modelo que juzgue. Se descartó:
un juez introduce su propia tasa de error, cuesta dinero por corrida y hace que
el número deje de ser reproducible.

Aquí la comprobación es DETERMINISTA y aprovecha una particularidad del dominio:
lo que este producto no puede inventar son CIFRAS, y las cifras son extraíbles
con una expresión regular. Si la respuesta dice «S/ 1.03» y ese número no está
en el contexto ni se deriva de él, es invención — sin ambigüedad y sin juez.

Lo que se pierde, dicho claro: esto NO mide si la respuesta es útil, ni si
razona bien, ni si atribuye causas que el dato no respalda. Mide una cosa
concreta y la mide sin margen de duda. Un número honesto y estrecho vale más que
uno amplio que nadie puede reproducir.

LOS CASOS ADVERSARIALES, Y POR QUÉ SE COMPRUEBAN POR LO QUE AFIRMAN
--------------------------------------------------------------------
Una pregunta con premisa falsa —«¿por qué BAJÓ el brócoli en febrero de 2025?»
cuando subió un 241,6 %— no se puede evaluar contando cifras: el modelo puede
tragarse la premisa y explicar una bajada inexistente sin escribir un solo
número inventado. El detector de invenciones no la ve.

Se comprueba con dos predicados, ambos subcadenas y ambos deterministas:

    debe_afirmar     al menos una de estas subcadenas está en la respuesta
    no_debe_afirmar  ninguna de estas subcadenas está en la respuesta

La comparación ignora tildes y mayúsculas.

DECISIÓN DELIBERADA: la premisa falsa se comprueba exigiendo la CORRECCIÓN
(`debe_afirmar: ["subio", "alza", ...]`), no prohibiendo la palabra «bajó». Una
respuesta correcta dice «no bajó, subió un 241,6 %» — y contiene «bajó». Prohibir
la palabra castigaría justo la respuesta buena. Se pide lo que la respuesta
correcta tiene que decir, no lo que la mala diría.

LAS TRES CATEGORÍAS, Y POR QUÉ SON TRES
----------------------------------------
Colapsar esto en un solo porcentaje escondería la incertidumbre:

    en_contexto   el número aparece literal en lo recuperado
    derivada      sale de dos números del contexto por resta o variación
                  porcentual — «subió 3 céntimos», «+3,0%»
    SIN RESPALDO  ni una cosa ni la otra

Solo la tercera es invención. La segunda es aritmética legítima sobre el dato, y
tratarla como error haría que el arnés castigara justo lo que se quiere.

Uso:
    python evals/run_generacion.py                 # necesita AI_API_KEY
    python evals/run_generacion.py --umbral 0.0    # falla si hay invención
    python evals/run_generacion.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from preciovivo import ai  # noqa: E402
from preciovivo.corpus import cargar_snapshot  # noqa: E402
from preciovivo.retrieval import Recuperador, catalogo_de  # noqa: E402

GOLD = AQUI / "retrieval_gold.json"
SNAPSHOT = AQUI.parent.parent / "web" / "data" / "snapshot.json"

# DeepSeek, USD por millón de tokens. Se declara aquí y no se lee de ningún
# sitio: si cambia el proveedor, este número miente y hay que actualizarlo a
# mano. Un coste calculado con precios viejos es peor que no calcularlo.
USD_POR_M_ENTRADA = 0.27
USD_POR_M_SALIDA = 1.10

# Tolerancia relativa al comparar una cifra de la respuesta con una derivación.
# 1% absorbe el redondeo del modelo ("+3,0%" por 2,999…%) sin dejar pasar
# invenciones: una cifra inventada rara vez cae a menos del 1% de una derivación
# legítima.
TOLERANCIA = 0.01

# QUÉ NÚMEROS CUENTAN, Y LO QUE COSTÓ ACERTAR
#
# La primera versión descartaba todo valor menor que 10 para no ahogarse en
# ruido ("2 días", "los 5 más baratos"). Descartaba, por tanto, TODOS LOS
# PRECIOS: S/ 1.03, S/ 4.87, un +3,0%. El arnés reportó 203 cifras afirmadas y
# cero invenciones, y ese cero no significaba nada — solo había mirado toneladas
# y fechas. La cifra que este producto no puede inventar era justamente la que no
# se estaba midiendo.
#
# Se detectó probando el detector contra invenciones conocidas, no leyendo su
# resultado. Un indicador que siempre sale perfecto es indistinguible de uno
# roto, y la única forma de separarlos es enseñarle algo que DEBE marcar.
#
# Regla actual: cuentan los decimales (un precio o un porcentaje casi nunca es
# entero redondo) y los enteros >= 10. Se descartan los años y los enteros de un
# dígito, que suelen ser conteos del propio texto y no datos.
#
# Límite conocido y aceptado: un precio entero inventado ("S/ 5") se escaparía.
# En esta serie los precios llevan decimales, así que el hueco es estrecho — pero
# existe y queda dicho.
_ANIOS = {float(a) for a in range(2000, 2100)}


def _numeros(texto: str) -> set[float]:
    """Los números de un texto, normalizados.

    Maneja las tres formas que conviven en este dominio: `1.03`, `1,03` y
    `2 355` con espacio de millares. El orden de las sustituciones importa —
    primero se quitan los separadores de millar, luego se unifica el decimal.
    """
    limpio = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", texto)
    fuera = set()
    for bruto in re.findall(r"-?\d+(?:[.,]\d+)?", limpio):
        try:
            v = abs(float(bruto.replace(",", ".")))
        except ValueError:
            continue
        if v in _ANIOS:
            continue
        if "." not in bruto and "," not in bruto and v < 10:
            continue
        fuera.add(round(v, 4))
    return fuera


def _derivable(x: float, base: set[float]) -> bool:
    """¿Sale `x` de dos números del contexto por aritmética simple?

    Se cubren las dos operaciones que el prompt pide de forma explícita —
    describir un movimiento y su magnitud— y ninguna más. Ampliar el repertorio
    haría más fácil «justificar» cualquier número, que es justo lo contrario de
    lo que este arnés busca.
    """
    cerca = lambda a, b: abs(a - b) <= max(TOLERANCIA * max(abs(b), 1.0), 0.005)  # noqa: E731
    for a in base:
        for b in base:
            if a == b:
                continue
            if cerca(x, abs(a - b)):
                return True
            if b and cerca(x, abs(100.0 * (a - b) / b)):
                return True
    return False


def clasificar_cifras(respuesta: str, contexto: str) -> dict:
    """Reparte las cifras de la respuesta en las tres categorías."""
    del_contexto = _numeros(contexto)
    en_contexto, derivadas, sin_respaldo = [], [], []
    for x in sorted(_numeros(respuesta)):
        if any(abs(x - c) <= 0.005 for c in del_contexto):
            en_contexto.append(x)
        elif _derivable(x, del_contexto):
            derivadas.append(x)
        else:
            sin_respaldo.append(x)
    return {"en_contexto": en_contexto, "derivadas": derivadas,
            "sin_respaldo": sin_respaldo}


_PRECIO = re.compile(r"S/\s*\d|\d+(?:[.,]\d+)?\s*soles", re.IGNORECASE)


def afirma_un_precio(respuesta: str) -> bool:
    """¿La respuesta pone precio a algo?

    Se usa solo en los casos de abstención. Una respuesta correcta a «¿cuánto
    cuesta la papaya?» explica que no está en el catálogo; una incorrecta
    responde con el precio de la papa por parecido de nombre, que es el fallo
    que el gold set lleva marcado desde el principio.
    """
    return bool(_PRECIO.search(respuesta))


def _plano(s: str) -> str:
    """Sin tildes y en minúscula, para comparar subcadenas sin sorpresas.

    El modelo escribe «subió» o «subio» según le dé, y la diferencia no dice
    nada sobre si acertó.
    """
    sin_tilde = unicodedata.normalize("NFKD", s)
    return "".join(c for c in sin_tilde if not unicodedata.combining(c)).lower()


def afirmaciones(respuesta: str, caso: dict) -> dict:
    """Comprueba `debe_afirmar` y `no_debe_afirmar` sobre la respuesta.

    Devuelve solo las claves que el caso declara: un caso sin predicados
    adversariales no aporta campos vacíos que luego haya que filtrar.
    """
    texto = _plano(respuesta)
    fuera = {}
    exigidas = caso.get("debe_afirmar")
    if exigidas:
        # cualquiera basta: son formas alternativas de decir lo mismo, no una
        # lista de requisitos acumulativos.
        fuera["afirmo"] = any(_plano(x) in texto for x in exigidas)
        fuera["afirmo_cual"] = [x for x in exigidas if _plano(x) in texto]
    prohibidas = caso.get("no_debe_afirmar")
    if prohibidas:
        fuera["dijo_prohibido"] = [x for x in prohibidas if _plano(x) in texto]
    return fuera


def correr(casos: list[dict], rec: Recuperador, catalogo: dict) -> list[dict]:
    filas = []
    for caso in casos:
        ctx = rec.recuperar(caso["pregunta"])
        contexto = ctx.a_prompt() if hasattr(ctx, "a_prompt") else str(ctx)
        salida = ai.answer_with_context(
            caso["pregunta"], contexto,
            [{"slug": s, "nombre": n} for s, n in catalogo.items()])

        texto = salida.get("texto") or ""
        fila = {
            "id": caso["id"],
            "categoria": caso["categoria"],
            "fuente": salida.get("fuente"),
            "cifras": clasificar_cifras(texto, contexto),
            "uso": salida.get("uso"),
            "texto": texto,
        }
        if caso.get("debe_abstenerse"):
            fila["abstuvo"] = not afirma_un_precio(texto)
        fila.update(afirmaciones(texto, caso))
        filas.append(fila)
    return filas


def resumir(filas: list[dict]) -> dict:
    total_cifras = sum(len(f["cifras"]["en_contexto"]) + len(f["cifras"]["derivadas"])
                       + len(f["cifras"]["sin_respaldo"]) for f in filas)
    inventadas = sum(len(f["cifras"]["sin_respaldo"]) for f in filas)
    con_abstencion = [f for f in filas if "abstuvo" in f]
    con_afirmacion = [f for f in filas if "afirmo" in f]
    usos = [f["uso"] for f in filas if f.get("uso")]
    ent = sum(u["entrada"] or 0 for u in usos)
    sal = sum(u["salida"] or 0 for u in usos)
    usd = ent / 1e6 * USD_POR_M_ENTRADA + sal / 1e6 * USD_POR_M_SALIDA
    # Una respuesta del fallback determinista no dice nada sobre el modelo: si
    # aparece, el arnés midió otra cosa y hay que decirlo en vez de promediarlo.
    sin_modelo = [f["id"] for f in filas if f["fuente"] != "llm-rag"]
    return {
        "casos": len(filas),
        "cifras_afirmadas": total_cifras,
        "cifras_sin_respaldo": inventadas,
        "tasa_invencion": (inventadas / total_cifras) if total_cifras else 0.0,
        "casos_con_invencion": [f["id"] for f in filas if f["cifras"]["sin_respaldo"]],
        "abstenciones_ok": sum(1 for f in con_abstencion if f["abstuvo"]),
        "abstenciones_esperadas": len(con_abstencion),
        "afirmaciones_ok": sum(1 for f in con_afirmacion if f["afirmo"]),
        "afirmaciones_esperadas": len(con_afirmacion),
        "casos_sin_la_afirmacion": [f["id"] for f in con_afirmacion if not f["afirmo"]],
        "casos_con_prohibido": {f["id"]: f["dijo_prohibido"]
                                for f in filas if f.get("dijo_prohibido")},
        "casos_sin_modelo": sin_modelo,
        "tokens_entrada": ent,
        "tokens_salida": sal,
        "usd_total": round(usd, 5),
        "usd_por_consulta": round(usd / len(usos), 6) if usos else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluación de generación de Precio Vivo")
    ap.add_argument("--umbral", type=float, default=None,
                    help="tasa de invención máxima; por encima, sale con código 1")
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--solo", help="correr un caso por id")
    args = ap.parse_args(argv)

    if not (os.environ.get("AI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        # No se degrada en silencio: sin clave no hay nada que medir, y decir
        # "0 invenciones" sobre respuestas de plantilla sería mentir con un
        # número. Código 0 para que CI pueda saltarlo sin fallar.
        print("SIN CLAVE (AI_API_KEY): la evaluación de generación necesita el "
              "modelo real. No se mide nada y no se inventa un número.")
        return 0

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    casos = gold["casos"]
    if args.solo:
        casos = [c for c in casos if c["id"] == args.solo]

    snap = cargar_snapshot(args.snapshot)
    rec = Recuperador.desde_snapshot(snap)
    catalogo = catalogo_de(snap)

    caso_por_id = {c["id"]: c for c in casos}
    filas = correr(casos, rec, catalogo)
    resumen = resumir(filas)

    if args.json:
        print(json.dumps({"resumen": resumen, "filas": filas},
                         ensure_ascii=False, indent=1))
    else:
        print(f"== Generación · {resumen['casos']} casos · embebedor real ==\n")
        for f in filas:
            c = f["cifras"]
            if c["sin_respaldo"]:
                marca = "!!"
            elif f.get("abstuvo") is False:
                marca = "ab"
            elif f.get("afirmo") is False or f.get("dijo_prohibido"):
                marca = "adv"
            else:
                marca = "   "
            print(f" {marca} {f['id']:32.32s} ctx={len(c['en_contexto']):2d} "
                  f"der={len(c['derivadas']):2d} SIN={len(c['sin_respaldo']):2d}"
                  + ("" if f["fuente"] == "llm-rag" else f"  [fuente={f['fuente']}]"))
            for x in c["sin_respaldo"]:
                print(f"      cifra sin respaldo: {x}")
            if f.get("afirmo") is False:
                print("      no dijo ninguna de: "
                      f"{caso_por_id[f['id']].get('debe_afirmar')}")
            if f.get("dijo_prohibido"):
                print(f"      dijo lo prohibido: {f['dijo_prohibido']}")
        print(f"\n cifras afirmadas   {resumen['cifras_afirmadas']}")
        print(f" sin respaldo       {resumen['cifras_sin_respaldo']}"
              f"  ({resumen['tasa_invencion']:.1%})")
        print(f" abstenciones       {resumen['abstenciones_ok']}/"
              f"{resumen['abstenciones_esperadas']}")
        if resumen["afirmaciones_esperadas"]:
            print(f" adversariales      {resumen['afirmaciones_ok']}/"
                  f"{resumen['afirmaciones_esperadas']} dijeron lo que debían")
        print(f" tokens             {resumen['tokens_entrada']:,} entrada · "
              f"{resumen['tokens_salida']:,} salida")
        print(f" coste              USD {resumen['usd_total']:.5f} en total · "
              f"{resumen['usd_por_consulta']:.6f} por consulta")
        if resumen["casos_sin_modelo"]:
            print(f" SIN MODELO         {resumen['casos_sin_modelo']}"
                  f"  <- estos no miden generación")

    if args.umbral is not None and resumen["tasa_invencion"] > args.umbral:
        print(f"\nFALLA: tasa de invención {resumen['tasa_invencion']:.1%} > "
              f"umbral {args.umbral:.1%}", file=sys.stderr)
        return 1
    if resumen["abstenciones_esperadas"] and (
            resumen["abstenciones_ok"] < resumen["abstenciones_esperadas"]):
        print("\nFALLA: alguna respuesta puso precio a un producto que no está "
              "en el catálogo.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
