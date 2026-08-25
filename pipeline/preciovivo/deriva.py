"""Deriva de distribucion: PSI y CSI sobre la serie real del GMML.

QUE SON, Y POR QUE SON DOS Y NO UNO
------------------------------------
Vienen del scoring crediticio, donde un modelo aprobado hace dos anos sigue
puntuando y nadie sabe si el mundo que vio al entrenar es el de hoy.

    PSI  Population Stability Index. Sobre la SALIDA del modelo -- aqui, la
         distribucion de precios que el sistema publica y pronostica.
    CSI  Characteristic Stability Index. El mismo calculo, pero sobre cada
         ENTRADA por separado.

Son dos porque responden preguntas distintas. Un PSI tranquilo con un CSI alto en
una caracteristica significa que el modelo aguanta por compensacion entre
variables, y eso es fragil: la proxima que se mueva lo tumba. Un solo numero
agregado esconde exactamente eso.

    PSI = suma sobre bins de  (a_i - e_i) * ln(a_i / e_i)

con e_i y a_i las FRACCIONES esperada y actual en el bin i.

UMBRALES, Y DE DONDE SALEN
---------------------------
    < 0,10   estable
    < 0,25   moderada, vigilar
    >= 0,25  significativa, revisar el modelo

Son la convencion de la industria crediticia, no una derivacion. Se declaran como
convencion en vez de presentarlos como si tuvieran una base estadistica: el PSI
no es un contraste de hipotesis y no lleva p-valor.

LOS DOS DETALLES QUE ROMPEN UNA IMPLEMENTACION INGENUA
-------------------------------------------------------
1. `ln(0)`. Si un bin queda vacio en la ventana actual, el termino es -infinito y
   el PSI entero se va a infinito por un solo bin sin datos. Se sustituye el cero
   por EPSILON. Es la practica estandar y **cambia el resultado**, asi que se
   declara en la salida en vez de esconderse.

2. Bins por CUANTILES de la referencia, no de ancho igual. Con ancho igual y una
   distribucion sesgada -- como los precios, con cola larga a la derecha -- casi
   todo cae en el primer bin y el indice no ve nada. Los cuantiles reparten la
   masa por construccion.

QUE NO ES ESTO
--------------
No es monitoreo en produccion: no hay alertas ni serie continua. Es el calculo
sobre historia real ya publicada, con ventanas que se eligen a mano. Los eventos
que detecta son reales y estan fechados; el aparato que los vigilaria en vivo no
existe todavia, y decirlo al reves seria inventar una capacidad.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Sustituto del cero al calcular ln(a/e). 1e-4 es la convencion habitual: lo
# bastante pequeno para no mover un PSI real, lo bastante grande para no
# convertir un bin vacio en un infinito.
EPSILON = 1e-4

UMBRAL_MODERADA = 0.10
UMBRAL_SIGNIFICATIVA = 0.25

# Si mas de esta fraccion de bins esta vacia, las dos ventanas apenas se solapan
# y el indice deja de ser interpretable.
#
# NO ES UN AJUSTE COSMETICO. La primera corrida sobre datos reales dio
# CSI(mes) = 6,10 "significativa", y era un artefacto: la ventana de referencia
# era julio-diciembre y la actual marzo-agosto, asi que la distribucion del mes
# difiere por DEFINICION de las ventanas, no porque el mundo haya cambiado.
#
# Y AQUI VA LO QUE NO SE PUEDE RESOLVER CON ARITMETICA. Una deriva autentica pero
# extrema produce el MISMO sintoma: si los precios se triplicaran de verdad, los
# bins de la referencia tambien quedarian vacios. Con los numeros a la vista el
# indice no puede separar "artefacto de como corte las ventanas" de "el mundo
# cambio tanto que ya no se parece". Por eso "degenerada" no significa "no hay
# deriva": significa QUE ESTE INDICE NO PUEDE DECIDIRLO y hay que mirar los datos.
# Etiquetarlo como significativa seria igual de falso que etiquetarlo estable.
FRACCION_BINS_VACIOS_DEGENERADA = 0.5


def clasificar(valor: float, bins_vacios: int = 0, bins: int = 0) -> str:
    if bins and bins_vacios / bins > FRACCION_BINS_VACIOS_DEGENERADA:
        return "degenerada"
    if valor < UMBRAL_MODERADA:
        return "estable"
    if valor < UMBRAL_SIGNIFICATIVA:
        return "moderada"
    return "significativa"


def _cuantiles(datos: list[float], n_bins: int) -> list[float]:
    """Cortes por cuantiles de la referencia. Devuelve n_bins-1 fronteras.

    Se deduplican: una distribucion con muchos valores repetidos -- por ejemplo
    un producto que pasa meses al mismo precio -- produce cortes iguales, y un
    bin de ancho cero no puede contener nada.
    """
    if not datos:
        return []
    orden = sorted(datos)
    n = len(orden)
    cortes = []
    for k in range(1, n_bins):
        # El corte es el ULTIMO valor del bin anterior, porque `_fracciones`
        # clasifica con `x > corte`. Escrito como `k*n//n_bins - 1` y no con
        # `round(k*(n-1)/n_bins)`: esa version daba el PRIMER valor del bin
        # siguiente para las k bajas y el ultimo del anterior para las altas,
        # y con datos discretos repartidos por igual colapsaba un corte --
        # cinco bins salian cuatro. Lo cazo una prueba contra aritmetica hecha
        # a mano; ninguna prueba de "no explota" lo habria visto.
        idx = max(0, k * n // n_bins - 1)
        cortes.append(orden[idx])
    return sorted(set(cortes))


def _fracciones(datos: list[float], cortes: list[float]) -> list[float]:
    """Fraccion de `datos` en cada bin definido por `cortes`."""
    n_bins = len(cortes) + 1
    cuenta = [0] * n_bins
    for x in datos:
        i = 0
        while i < len(cortes) and x > cortes[i]:
            i += 1
        cuenta[i] += 1
    total = len(datos) or 1
    return [c / total for c in cuenta]


@dataclass
class Resultado:
    valor: float
    clase: str
    n_ref: int
    n_act: int
    bins: int
    bins_vacios: int = 0
    por_bin: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        if self.clase == "degenerada":
            aviso = (f"  ({self.bins_vacios} de {self.bins} bins vacios: las "
                     "ventanas apenas se solapan. Puede ser un artefacto de como "
                     "se cortaron, o deriva tan grande que el indice ya no la "
                     "puede medir. Hay que mirar los datos)")
        elif self.bins_vacios:
            aviso = f"  ({self.bins_vacios} bin(s) vacios sustituidos por epsilon)"
        else:
            aviso = ""
        return (f"{self.valor:.4f} [{self.clase}]  "
                f"n_ref={self.n_ref} n_act={self.n_act} bins={self.bins}{aviso}")


def psi(referencia: list[float], actual: list[float], n_bins: int = 10) -> Resultado:
    """Population Stability Index entre dos ventanas.

    NO es simetrico: psi(a, b) != psi(b, a). La referencia define los bins, asi
    que cambiar el orden cambia la particion. Es correcto -- la pregunta es
    "cuanto se ha movido lo actual respecto a lo que el modelo vio", no "cuanto
    difieren entre si".
    """
    if not referencia or not actual:
        return Resultado(0.0, "sin datos", len(referencia), len(actual), 0)

    cortes = _cuantiles(referencia, n_bins)
    e = _fracciones(referencia, cortes)
    a = _fracciones(actual, cortes)

    total, vacios, detalle = 0.0, 0, []
    for i, (ei, ai) in enumerate(zip(e, a, strict=True)):
        if ei == 0 or ai == 0:
            vacios += 1
        ei_s = max(ei, EPSILON)
        ai_s = max(ai, EPSILON)
        aporte = (ai_s - ei_s) * math.log(ai_s / ei_s)
        total += aporte
        detalle.append({"bin": i, "esperado": ei, "actual": ai, "aporte": aporte})

    return Resultado(total, clasificar(total, vacios, len(e)),
                     len(referencia), len(actual), len(e), vacios, detalle)


def csi(referencia: dict[str, list[float]], actual: dict[str, list[float]],
        n_bins: int = 10) -> dict[str, Resultado]:
    """El mismo calculo, una caracteristica de entrada a la vez.

    Devuelve un resultado POR caracteristica y nunca los promedia: promediar
    esconde justo el caso peligroso, una variable muy movida compensada por otras
    quietas.
    """
    comunes = [k for k in referencia if k in actual]
    return {k: psi(referencia[k], actual[k], n_bins) for k in comunes}


def peor(resultados: dict[str, Resultado]) -> tuple[str, Resultado] | None:
    """La caracteristica que mas se movio, ignorando las degeneradas.

    Una degenerada siempre tendria el valor mas alto y taparia a la que de
    verdad se movio, que es lo unico que se queria saber.
    """
    utiles = {k: v for k, v in resultados.items() if v.clase != "degenerada"}
    if not utiles:
        return None
    k = max(utiles, key=lambda x: utiles[x].valor)
    return k, utiles[k]
