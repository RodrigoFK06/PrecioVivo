# Precio Vivo - actualizacion diaria.
# Descarga el reporte del dia, recalcula el pronostico y publica el snapshot
# (git push -> Vercel redeploya). Programar con el Programador de tareas de
# Windows (diario, p. ej. 08:00). Debe correr desde una IP que gob.pe no bloquee
# (tu maquina sirve; un runner de datacenter podria estar bloqueado por el WAF).
# NOTA: archivo en ASCII a proposito - PowerShell 5.1 lee .ps1 como ANSI y los
# acentos/guiones largos UTF-8 rompen el parser.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "pipeline\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

Push-Location (Join-Path $root "pipeline")
try {
    & $py -m preciovivo.ingest --latest 5    # nuevos dias habiles (idempotente)
    & $py -m preciovivo.ingest --forecast    # recalcula pronosticos (~14 min) + cachea
    & $py -m preciovivo.export               # escribe ../web/data/snapshot.json
} finally {
    Pop-Location
}

Set-Location $root
git add web/data/snapshot.json
$fecha = Get-Date -Format "yyyy-MM-dd"
# Si no hubo cambios (p. ej. fin de semana sin reporte nuevo), no falla la tarea.
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "data: snapshot $fecha"
    git push
    Write-Host "Publicado snapshot $fecha"
} else {
    Write-Host "Sin cambios en el snapshot ($fecha) - nada que publicar."
}
