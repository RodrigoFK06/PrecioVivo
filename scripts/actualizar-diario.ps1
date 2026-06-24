# Precio Vivo — actualización diaria.
# Descarga el reporte del día, recalcula el pronóstico y publica el snapshot
# (git push -> Vercel redeploya). Programar con el Programador de tareas de
# Windows (diario, p. ej. 07:00). Debe correr desde una IP que gob.pe no bloquee
# (tu máquina sirve; un runner de datacenter podría estar bloqueado por el WAF).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "pipeline\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

Push-Location (Join-Path $root "pipeline")
try {
    & $py -m preciovivo.ingest --latest 5    # nuevos días hábiles (idempotente)
    & $py -m preciovivo.ingest --forecast    # recalcula pronósticos (~14 min) + cachea
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
    Write-Host "Sin cambios en el snapshot ($fecha) — nada que publicar."
}
