# ===========================================================================
# SUPERADO. Ya no lo ejecuta ninguna tarea programada.
#
# Todo lo que hace este script vive ahora en AWS (Step Functions, lunes a
# viernes 08:00 Lima; ver docs/aws.md) MENOS el contraste con SISAP, porque
# sistemas.midagri.gob.pe no acepta conexiones desde AWS. Medido:
#     desde esta maquina   200, 337.994 bytes, 0,3 s
#     desde Lambda         ConnectTimeout a los 60 s
#
# Esa unica pieza quedo en scripts/sisap-diario.ps1, que es a lo que apunta
# ahora la tarea PrecioVivo-Diario (07:30, antes de que arranque AWS).
#
# Se conserva porque documenta el pipeline completo en un solo archivo legible y
# porque sigue sirviendo para correr todo a mano si AWS estuviera caido. NO lo
# programes: publicaria en el mismo repositorio que la tuberia de AWS y los dos
# se pisarian.
# ===========================================================================
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
    # --sisap va DESPUES de --latest a proposito, pero ese orden no basta y no es
    # donde estaba el problema: SISAP publica ~06:30 y tambien los sabados,
    # mientras que el GMML no publica fines de semana, asi que la fecha de SISAP
    # va por delante de nuestro ultimo 335 pase lo que pase. El contraste se
    # resuelve en sisap._resolver_objetivo, que usa la columna "Precio Ayer" de
    # SISAP contra nuestro ultimo dia GMML: ambas describen la misma jornada.
    & $py -m preciovivo.ingest --sisap       # pollo vivo (AVES) + cross-check GMML (no bloquea)
    & $py -m preciovivo.ingest --forecast    # recalcula pronosticos (~14 min) + cachea
    & $py -m preciovivo.export               # escribe ../web/data/snapshot.json
    # Indice RAG. --index-solo-reciente NO reescribe la parte historica: el corpus
    # pasado es inmutable, y reescribirlo a diario engordaria el repo ~2 MB por dia
    # (git guarda cada blob binario entero). Ver README, seccion RAG.
    # Si falla (sin clave de embeddings, p. ej.) no debe tumbar la publicacion del
    # snapshot: el sitio degrada a catalogo-en-contexto y sigue respondiendo.
    try {
        & $py -m preciovivo.ingest --index --index-solo-reciente
    } catch {
        Write-Warning "Indice RAG no actualizado: $_"
    }
} finally {
    Pop-Location
}

Set-Location $root
git add web/data/snapshot.json web/data/rag-reciente.bin web/data/rag-reciente.json.gz
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
