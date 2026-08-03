param(
    [string]$TaskName = 'PCM-Web-Server-Startup',
    [string]$HealthUrl = 'http://127.0.0.1:5000/healthz',
    [int]$TimeoutSeconds = 8
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $repoRoot 'logs'
$logFile = Join-Path $logDir 'watchdog.log'

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-WatchdogLog([string]$message) {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $message" |
        Out-File -FilePath $logFile -Append -Encoding utf8
}

function Test-PcmHealth {
    try {
        $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec $TimeoutSeconds -Method Get
        return $response.status -eq 'ok'
    }
    catch {
        Write-WatchdogLog "Health probe failed: $($_.Exception.Message)"
        return $false
    }
}

if (Test-PcmHealth) {
    exit 0
}

# ตรวจซ้ำเพื่อไม่ Restart จากเครือข่ายสะดุดชั่วคราว
Start-Sleep -Seconds 5
if (Test-PcmHealth) {
    exit 0
}

Write-WatchdogLog "Service unhealthy twice; restarting scheduled task $TaskName"
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 12
    if (Test-PcmHealth) {
        Write-WatchdogLog 'Service recovered successfully'
        exit 0
    }
    Write-WatchdogLog 'Service did not recover after restart'
    exit 1
}
catch {
    Write-WatchdogLog "Restart failed: $($_.Exception.Message)"
    exit 1
}
