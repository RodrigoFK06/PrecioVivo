# Precio Vivo - relevo de SISAP.
#
# QUE HACE Y POR QUE ES TAN CORTO
# --------------------------------
# Todo el pipeline diario vive ya en AWS (Step Functions, ver docs/aws.md). Todo
# menos una cosa: el contraste con SISAP.
#
# sistemas.midagri.gob.pe NO acepta conexiones desde AWS. Medido en los dos
# sentidos el 2026-08-19:
#     desde esta maquina   200, 337.994 bytes, 0,3 s, PDF valido
#     desde Lambda         ConnectTimeout a los 60 s, ni abre el TCP
#
# Asi que esta maquina se queda como unico punto con acceso a esa fuente, y su
# unico trabajo es de relevo: baja el estado de AWS, corre SISAP, y devuelve el
# resultado a AWS. Nada mas. La cosecha, el forecast, el export, el indice RAG y
# la publicacion ya no pasan por aqui.
#
# EL ORDEN IMPORTA
# ----------------
# Esto tiene que correr ANTES que la maquina de estados de AWS (08:00 Lima),
# porque el paso de export lee sisap_check.json para adjuntar el bloque
# `verificacion` al snapshot. SISAP publica ~06:30, asi que 07:30 deja margen
# para las dos cosas.
#
# Programar con el Programador de tareas de Windows, diario a las 07:30.
#
# NOTA: archivo en ASCII a proposito - PowerShell 5.1 lee .ps1 como ANSI y los
# acentos/guiones largos UTF-8 rompen el parser.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "pipeline\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

# ESPERAR A QUE HAYA RED ANTES DE EMPEZAR
#
# Lo que paso el primer dia de operacion autonoma: la maquina estaba dormida a
# las 07:30, Windows no la desperto (WakeToRun estaba en False) y la ejecuto a
# las 09:10 al despertar, con la pila de red aun sin levantar. El primer
# `aws s3 cp` fallo en seco y la tarea salio con codigo 1.
#
# El diagnostico no es "la red falla a veces": es que despertar y tener red no
# son el mismo instante. Esperar cuesta unos segundos y ahorra un dia sin
# contraste.
function Esperar-Red([int]$segundos = 120) {
    $limite = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $limite) {
        if (Test-Connection -ComputerName "s3.amazonaws.com" -Count 1 -Quiet -ErrorAction SilentlyContinue) {
            return $true
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

if (-not (Esperar-Red 120)) {
    # Se sale con error a proposito: sin red no hay nada que hacer, y salir en
    # verde dejaria el fallo invisible. El dead-man's-switch lo recogera si esto
    # se repite tres dias habiles seguidos.
    throw "Sin conectividad tras 120 s de espera; el relevo de SISAP no corre hoy."
}

$bucket = "preciovivofase1-snapshotbucketb2bf31d3-cg1401w9p7gp"
$db = Join-Path $root "data\preciovivo.db"
$check = Join-Path $root "data\sisap_check.json"

# 1. La BD de AWS es la fuente de verdad. Se baja entera y se pisa la local: el
#    cross-check compara los precios de SISAP contra los del reporte 335, y esos
#    los carga AWS. Una BD local desactualizada daria un contraste contra datos
#    viejos, que es peor que no tener contraste.
aws s3 cp "s3://$bucket/estado/preciovivo.db" $db --no-progress
if ($LASTEXITCODE -ne 0) { throw "no se pudo bajar la BD de S3" }

Push-Location (Join-Path $root "pipeline")
try {
    # 2. SISAP: ingesta AVES VIVAS (pollo vivo, que solo esta aqui) y escribe
    #    sisap_check.json. No bloquea: si SISAP no publico todavia, el modulo lo
    #    reporta y sale sin tocar los datos del 335.
    & $py -m preciovivo.ingest --sisap
} finally {
    Pop-Location
}

# 3. Devolver a AWS lo producido. La BD tambien, porque --sisap upserta AVES.
#
#    Sin escritura condicional a proposito: aqui no hay ETag que comparar desde
#    PowerShell sin complicar el script, y el riesgo real es bajo porque este
#    paso corre a las 07:30 y la maquina de estados a las 08:00. Si algun dia se
#    solapan, el sintoma seria perder la ingesta de AVES de ese dia, no corromper
#    los precios: la tabla de precios del 335 la escribe solo AWS.
if (Test-Path $check) {
    aws s3 cp $check "s3://$bucket/estado/sisap_check.json" --no-progress
    if ($LASTEXITCODE -ne 0) { throw "no se pudo subir sisap_check.json" }
    aws s3 cp $db "s3://$bucket/estado/preciovivo.db" --no-progress
    if ($LASTEXITCODE -ne 0) { throw "no se pudo devolver la BD a S3" }
    Write-Host "SISAP relevado a AWS ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))"
} else {
    # No es un fallo: SISAP puede no haber publicado aun, o el PDF puede no traer
    # filas utiles. El snapshot sale sin bloque `verificacion` y el sitio lo
    # ignora. Se dice en vez de fallar en silencio.
    Write-Warning "No se genero sisap_check.json - el snapshot de hoy saldra sin verificacion"
}
