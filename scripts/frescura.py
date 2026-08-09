"""Dead-man's-switch: avisa cuando el pipeline se perdió un día hábil.

POR QUÉ HACE FALTA
------------------
El modo de falla de esta clase de proyecto no es caerse: es dejar de
actualizarse en silencio. El sitio sigue en pie, responde 200, muestra precios
con formato impecable — de hace once días. Nadie se entera hasta que alguien
mira la fecha, y para entonces la credibilidad ya está gastada.

Un chequeo de "¿el servicio responde?" no detecta esto. Este script chequea lo
único que importa: cuántos DÍAS HÁBILES pasaron desde el último dato.

Días hábiles, no naturales: el GMML no publica sábados ni domingos, así que
medir en días naturales daría falsas alarmas cada lunes y toleraría de más los
viernes. Los feriados peruanos se descuentan con `holidays`, que ya es
dependencia del pipeline (lo usa forecast.py).

Uso:
    python scripts/frescura.py                  # sale 1 si está viejo
    python scripts/frescura.py --umbral 3       # tolerancia en días hábiles
    python scripts/frescura.py --json           # para CI
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SNAPSHOT = RAIZ / "web" / "data" / "snapshot.json"

# Cuántos días hábiles de atraso se toleran antes de alertar. Dos: uno absorbe
# que el reporte del día todavía no salga cuando corre el chequeo; a partir de
# dos, algo se rompió de verdad.
UMBRAL_DEFAULT = 2


def _feriados(anios: list[int]) -> set[date]:
    """Feriados peruanos. Si `holidays` no está, se sigue sin ellos.

    Degradar es correcto acá: sin feriados el chequeo es un poco más estricto
    (puede alertar de más tras un feriado), y una alerta de más es infinitamente
    preferible a no alertar cuando el pipeline se murió.
    """
    try:
        import holidays

        return set(holidays.country_holidays("PE", years=anios))
    except Exception:
        return set()


def dias_habiles_entre(desde: date, hasta: date) -> int:
    """Días hábiles (lunes a viernes, sin feriados peruanos) en (desde, hasta]."""
    if hasta <= desde:
        return 0
    feriados = _feriados(sorted({desde.year, hasta.year}))
    n, d = 0, desde + timedelta(days=1)
    while d <= hasta:
        if d.weekday() < 5 and d not in feriados:
            n += 1
        d += timedelta(days=1)
    return n


def revisar(ruta: Path = SNAPSHOT, umbral: int = UMBRAL_DEFAULT,
            hoy: date | None = None) -> dict:
    hoy = hoy or datetime.now(timezone.utc).date()
    if not ruta.exists():
        return {"estado": "sin_snapshot", "ok": False,
                "detalle": f"No existe {ruta}."}

    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        ultima = date.fromisoformat(datos["latestFecha"])
    except Exception as e:  # noqa: BLE001 - un snapshot ilegible también es fallo
        return {"estado": "snapshot_ilegible", "ok": False, "detalle": str(e)}

    atraso = dias_habiles_entre(ultima, hoy)
    ok = atraso <= umbral
    return {
        "estado": "ok" if ok else "desactualizado",
        "ok": ok,
        "ultima_fecha": ultima.isoformat(),
        "hoy": hoy.isoformat(),
        "dias_habiles_de_atraso": atraso,
        "umbral": umbral,
        "productos": datos.get("productCount"),
        "detalle": (f"Último dato del {ultima.isoformat()}: {atraso} día(s) hábil(es) "
                    f"de atraso (umbral {umbral})."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Chequeo de frescura de Precio Vivo")
    ap.add_argument("--umbral", type=int, default=UMBRAL_DEFAULT,
                    help="Días hábiles de atraso tolerados.")
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = revisar(Path(args.snapshot), args.umbral)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"[{r['estado']}] {r['detalle']}")
    if not r["ok"]:
        print("El pipeline se perdió días hábiles. Revisa la tarea programada, "
              "la fuente (gob.pe) y el harvester.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
