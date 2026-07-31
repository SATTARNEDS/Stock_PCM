param(
    [string]$ListenAddress = '0.0.0.0:5000'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'my_factory_app'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$waitressServer = Join-Path $appDir 'waitress_server.py'
$listenAddress = if ($env:PCM_LISTEN_ADDRESS) { $env:PCM_LISTEN_ADDRESS } else { $ListenAddress }

if (-not (Test-Path $appDir)) {
    Write-Error "ไม่พบโฟลเดอร์ my_factory_app: $appDir"
}

if (-not (Test-Path $venvPython)) {
    Write-Error "ไม่พบ Python ใน .venv: $venvPython"
}
if (-not (Test-Path $waitressServer)) {
    Write-Error "ไม่พบ waitress_server.py"
}

if ($listenAddress -notmatch '^(?<host>[^:]+):(?<port>\d+)$') {
    Write-Error "ListenAddress ต้องอยู่ในรูป host:port เช่น 0.0.0.0:5000"
}

Set-Location $appDir
$env:FLASK_DEBUG = '0'
& $venvPython $waitressServer --host $Matches.host --port $Matches.port --threads 12
exit $LASTEXITCODE
