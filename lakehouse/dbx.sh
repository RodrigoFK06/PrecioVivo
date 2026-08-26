#!/usr/bin/env bash
# Envoltorio de la CLI de Databricks.
#
# winget la instala en una ruta larga y no la deja en el PATH de esta sesion
# hasta reiniciar la shell. Esto la resuelve una vez para que el resto de
# scripts -- y el agente -- no repitan la ruta ni dependan de que el PATH este
# refrescado.
#
#     ./lakehouse/dbx.sh --version
#     ./lakehouse/dbx.sh workspace list /
#
# SOBRE LA AUTENTICACION, QUE NO PASA POR AQUI
# ---------------------------------------------
# Databricks marca los tokens personales (PAT) como legacy y recomienda OAuth.
# Conviene por una razon que no es de moda: con OAuth el login lo hace UNA
# PERSONA en SU navegador, y el secreto acaba en ~/.databrickscfg sin que nadie
# lo escriba, lo pegue en un chat ni lo deje en un archivo del repositorio.
#
# El login se hace UNA vez, a mano:
#
#     ./lakehouse/dbx.sh auth login --host https://TU-WORKSPACE.cloud.databricks.com
#
# A partir de ahi la CLI usa el token guardado y este script funciona sin que
# ninguna credencial pase por el historial de comandos.
#
# EL RODEO DEL HOST, QUE NO ES CAPRICHO
# --------------------------------------
# Con el host escrito en el perfil de ~/.databrickscfg, la CLI falla:
#
#     Error: default auth: cannot configure default credentials
#     Config: host=..., account_id=62fa..., workspace_id=7474...
#
# Con el MISMO host en la variable DATABRICKS_HOST, funciona. La diferencia esta
# en el account_id: al resolver desde el perfil, la CLI consulta metadatos del
# host, descubre la cuenta e intenta autenticacion a NIVEL DE CUENTA, donde no
# hay token -- el login OAuth guardo uno de workspace. Por la variable de entorno
# se salta ese paso.
#
# Asi que el host se lee del archivo (una sola fuente de verdad) y se exporta
# como variable. El token sigue en el almacen seguro del sistema y no pasa por
# aqui: la URL de un workspace no es un secreto.
# Git Bash traduce los argumentos que empiezan por "/" a rutas de Windows, asi
# que `workspace list /` llegaba como `C:/Program Files/Git/`. MSYS_NO_PATHCONV
# lo desactiva para esta invocacion.
export MSYS_NO_PATHCONV=1

set -euo pipefail

if [ -z "${DATABRICKS_HOST:-}" ] && [ -f "$HOME/.databrickscfg" ]; then
    DATABRICKS_HOST="$(grep -m1 '^host' "$HOME/.databrickscfg" | cut -d= -f2- | tr -d ' \r')"
    export DATABRICKS_HOST
fi

RAIZ_WINGET="$LOCALAPPDATA/Microsoft/WinGet/Packages"
CLI="$(find "$RAIZ_WINGET" -maxdepth 2 -iname 'databricks.exe' 2>/dev/null | head -1)"

if [ -z "${CLI:-}" ]; then
    CLI="$(command -v databricks || true)"
fi

if [ -z "${CLI:-}" ]; then
    echo "ERROR: no encuentro la CLI de databricks." >&2
    echo "  Instalala con: winget install --id Databricks.DatabricksCLI" >&2
    exit 127
fi

exec "$CLI" "$@"
