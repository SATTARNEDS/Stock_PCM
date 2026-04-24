$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'my_factory_app'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $appDir)) {
    Write-Error "ไม่พบโฟลเดอร์ my_factory_app: $appDir"
}

if (-not (Test-Path $venvPython)) {
    Write-Error "ไม่พบ Python ใน .venv: $venvPython"
}

Set-Location $appDir
& $venvPython app.py
