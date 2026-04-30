param(
    [int]$KeepLatest = 30
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'my_factory_app'
$dbPath = Join-Path $appDir 'factory_stock.db'
$backupDir = Join-Path $appDir 'backups'

if (-not (Test-Path $dbPath)) {
    throw "ไม่พบฐานข้อมูล: $dbPath"
}

if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupPath = Join-Path $backupDir "factory_stock_$timestamp.db"

Copy-Item -Path $dbPath -Destination $backupPath -Force
Write-Host "สำรองฐานข้อมูลเรียบร้อย: $backupPath" -ForegroundColor Green

$oldBackups = Get-ChildItem -Path $backupDir -Filter 'factory_stock_*.db' | Sort-Object LastWriteTime -Descending
if ($oldBackups.Count -gt $KeepLatest) {
    $toDelete = $oldBackups | Select-Object -Skip $KeepLatest
    foreach ($file in $toDelete) {
        Remove-Item -Path $file.FullName -Force
    }
    Write-Host "ลบ backup เก่าแล้ว: $($toDelete.Count) ไฟล์" -ForegroundColor Yellow
}
