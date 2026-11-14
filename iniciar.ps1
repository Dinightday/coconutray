$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    Write-Error 'Prepare o ambiente conforme o README.md antes de iniciar.'
}
& '.\.venv\Scripts\python.exe' 'run.py'
