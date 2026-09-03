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
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from preciovivo import ai  # noqa: E402
from preciovivo.corpus import cargar_snapshot  # noqa: E402
from preciovivo.embeddings import get_embedder  # noqa: E402
from preciovivo.retrieval import Recuperador, catalogo_de  # noqa: E402

GOLD = AQUI / "retrieval_gold.json"
SNAPSHOT = AQUI.parent.parent / "web" / "data" / "snapshot.json"

# COSTE. Las dos constantes que había aquí -0,27 de entrada y 1,10 de salida-
# eran precios viejos de `deepseek-chat` y SOBRESTIMABAN un 50 %: reportaban
# 0,14 USD para una corrida que cuesta 0,09. El propio comentario avisaba de que
# se quedarían obsoletas, y se quedaron.
#
# Ahora es UNA tarifa mezclada, y de procedencia comprobable: sale de la página
# de consumo real de la cuenta el 2026-09-03 — 507 peticiones, 658 849 tokens,
# 0,14 USD — o sea 0,2125 USD por millón, sobre `deepseek-chat` (que la API
# resuelve hoy a `deepseek-v4-flash`).
#
# POR QUÉ UNA SOLA Y NO DOS. Esa página no desglosa entrada y salida, así que
# dos constantes exigirían inventarme el reparto, que es exactamente el error
# que se acaba de corregir. Una cifra derivada de un consumo medido vale más que
# dos que no puedo sostener.
#
# LÍMITE DECLARADO: al ser mezclada, una corrida con mucha más salida de lo
# habitual queda SUBESTIMADA. La verdad exacta son los tokens, que este arnés ya
# imprime aparte; el USD es la comodidad, no la medida.
#
# Y OJO CON EL DÍA: desde el 2026-08-23 DeepSeek aplica tarifa de valle todo el
# fin de semana (hora de Pekín). Una corrida de sábado sale más barata que este
# número; una de martes por la tarde, no.
USD_POR_M_MEZCLADO = 0.2125

# LA COMPROBACION DE CIFRAS VIVE EN `preciovivo.verificador`.
#
# Estaba aqui, y solo aqui: el sistema sabia reconocer una cifra inventada pero
# solo lo hacia DESPUES, en una corrida de evaluacion, sobre los casos que
# tuvieran AI_API_KEY. Ahora la misma funcion corre EN LINEA sobre cada
# respuesta (`ai.answer_with_context`) y este arnes la importa en vez de
# mantener su copia.
#
# Importa y no reimplementa a proposito: si el arnes midiera con una definicion
# de "cifra sin respaldo" y el producto actuara con otra, la evaluacion estaria
# describiendo un sistema distinto del que responde.
from preciovivo.verificador import (  # noqa: E402
    TOLERANCIA,
    afirma_un_precio,
    clasificar_cifras,
)

__all__ = ["TOLERANCIA", "afirma_un_precio", "clasificar_cifras"]


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
            "verificacion": salida.get("verificacion") or {},
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
    usd = (ent + sal) / 1e6 * USD_POR_M_MEZCLADO
    # Una respuesta del fallback determinista no dice nada sobre el modelo: si
    # aparece, el arnés midió otra cosa y hay que decirlo en vez de promediarlo.
    sin_modelo = [f["id"] for f in filas if f["fuente"] != "llm-rag"]

    # LA INVENCION SE MIDE ANTES DEL ARREGLO, NO DESPUES.
    #
    # Desde que `ai.answer_with_context` verifica en linea, `tasa_invencion`
    # sobre el texto ENTREGADO es 0,000 POR CONSTRUCCION: el verificador
    # reintenta y, si el modelo reincide, degrada al fallback determinista. La
    # metrica dejo de poder fallar -- exactamente el defecto que este
    # repositorio persigue en todas partes: un indicador que siempre sale
    # perfecto es indistinguible de uno roto.
    #
    # Se conserva porque sigue siendo la promesa al usuario (si algun dia no da
    # 0, el verificador tiene un agujero), pero la cifra que mide AL MODELO es
    # `tasa_intervencion`: cuantas veces el PRIMER intento traia una cifra sin
    # respaldo.
    intervenidos = [f for f in filas
                    if (f.get("verificacion") or {}).get("sin_respaldo_inicial")]
    estados = {}
    for f in filas:
        e = (f.get("verificacion") or {}).get("estado")
        if e:
            estados[e] = estados.get(e, 0) + 1

    return {
        "casos": len(filas),
        "cifras_afirmadas": total_cifras,
        "cifras_sin_respaldo": inventadas,
        "tasa_invencion": (inventadas / total_cifras) if total_cifras else 0.0,
        "casos_con_invencion": [f["id"] for f in filas if f["cifras"]["sin_respaldo"]],
        "casos_intervenidos": [f["id"] for f in intervenidos],
        "tasa_intervencion": (len(intervenidos) / len(filas)) if filas else 0.0,
        "verificacion_estados": estados,
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
    # POR QUE ESTA BANDERA EXISTE, Y POR QUE EL DEFECTO NO ES `fake`.
    #
    # `run_retrieval.py` usa `fake` por defecto: gratis, sin red, determinista.
    # Aqui NO puede ser el defecto -- medir si el modelo inventa cifras sobre un
    # contexto recuperado con vectores de juguete mide otra cosa. El contexto
    # tiene que ser el que el producto entregaria de verdad.
    #
    # Pero el coste estaba OCULTO: construir el indice con el proveedor real
    # consume cuota de embeddings antes de la primera llamada al modelo, y nada
    # lo advertia. Ahora se avisa y se puede elegir.
    ap.add_argument("--embedder", default="auto",
                    choices=["auto", "api", "bedrock", "local", "fake"],
                    help="embebedor del indice (default auto: usa el real y "
                         "consume cuota)")
    ap.add_argument("--exigir-modelo", action="store_true",
                    help="falla si no hay clave, en vez de saltarse la medicion")
    args = ap.parse_args(argv)

    if not (os.environ.get("AI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        # No se degrada en silencio: sin clave no hay nada que medir, y decir
        # "0 invenciones" sobre respuestas de plantilla seria mentir con un
        # numero.
        print("SIN CLAVE (AI_API_KEY): la evaluacion de generacion necesita el "
              "modelo real. No se mide nada y no se inventa un numero.")
        # POR QUE EXISTE `--exigir-modelo`.
        #
        # Salir con 0 deja que CI siga en verde sin haber medido nada. La
        # mayoria del valor adversarial de este gold set vive en la capa de
        # generacion, asi que una clave ausente o caducada apagaba en silencio
        # la parte mas cara de la suite y nada chirriaba: exactamente el fallo
        # que este repositorio persigue en todas partes -- un indicador que
        # siempre sale perfecto es indistinguible de uno roto.
        #
        # Con la bandera, la rama principal exige que la medicion OCURRA. Sin
        # ella (forks, PRs de fuera) se sigue pudiendo saltar.
        if args.exigir_modelo:
            print("FALLA: --exigir-modelo y no hay AI_API_KEY. La evaluacion "
                  "adversarial de generacion NO se ha ejecutado.", file=sys.stderr)
            return 1
        return 0

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    casos = gold["casos"]
    if args.solo:
        casos = [c for c in casos if c["id"] == args.solo]

    snap = cargar_snapshot(args.snapshot)
    emb = get_embedder(args.embedder)
    if args.embedder in ("auto", "api", "bedrock"):
        print(f"embebedor: {emb.firma}", file=sys.stderr)
        print("  AVISO: construir el indice con este embebedor CONSUME CUOTA de "
              "embeddings.", file=sys.stderr)
        print("  Lo ya calculado sale de la cache; solo se pagan los chunks "
              "nuevos. Con --embedder local no se consume nada.", file=sys.stderr)
    elif args.embedder == "fake":
        print("embebedor: fake -- el contexto NO es el que entregaria el "
              "producto. La invencion de cifras medida asi no es representativa.",
              file=sys.stderr)
    rec = Recuperador.desde_snapshot(snap, embedder=emb)
    catalogo = catalogo_de(snap)

    caso_por_id = {c["id"]: c for c in casos}
    filas = correr(casos, rec, catalogo)
    resumen = resumir(filas)

    if args.json:
        # stdout se codifica en cp1252 en Windows y `ensure_ascii=False`
        # perdia los acentos al redirigir: "pronostico" con tilde acababa como
        # U+FFFD y las comprobaciones por subcadena del gold set no encontraban
        # nada. El JSON seguia siendo valido, con el contenido roto.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps({"resumen": resumen, "filas": filas},
                         ensure_ascii=False, indent=1))
    else:
        print(f"== Generación · {resumen['casos']} casos · {emb.firma} ==\n")
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
        print(f" intervino el verificador  {len(resumen['casos_intervenidos'])}"
              f"/{resumen['casos']} casos "
              f"({resumen['tasa_intervencion']:.1%})   <- mide AL MODELO")
        if resumen["verificacion_estados"]:
            print(f"   estados           {resumen['verificacion_estados']}")
        print(f" sin respaldo       {resumen['cifras_sin_respaldo']}"
              f"  ({resumen['tasa_invencion']:.1%})")
        print(f" abstenciones       {resumen['abstenciones_ok']}/"
              f"{resumen['abstenciones_esperadas']}")
        if resumen["afirmaciones_esperadas"]:
            print(f" adversariales      {resumen['afirmaciones_ok']}/"
                  f"{resumen['afirmaciones_esperadas']} dijeron lo que debían")
        print(f" tokens             {resumen['tokens_entrada']:,} entrada · "
              f"{resumen['tokens_salida']:,} salida")
        # `usd_por_consulta` es None si no hubo ni una llamada al modelo (cero
        # casos, p. ej. un `--solo` con el id mal escrito). Formatearlo con
        # `:.6f` reventaba con TypeError en vez de decir que no habia nada que
        # medir.
        por_consulta = resumen["usd_por_consulta"]
        cola = (f"{por_consulta:.6f} por consulta" if por_consulta is not None
                else "sin consultas al modelo")
        print(f" coste              USD {resumen['usd_total']:.5f} en total · "
              f"{cola}")
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
