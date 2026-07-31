param(
    [int]$KeepLatest = 30
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'my_factory_app'
$dbPath = Join-Path $appDir 'factory_stock.db'
$backupDir = Join-Path $appDir 'backups'
$backupTool = Join-Path $appDir 'backup_database.py'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $dbPath)) {
    throw "ไม่พบฐานข้อมูล: $dbPath"
}
if (-not (Test-Path $backupTool)) {
    throw "ไม่พบเครื่องมือสำรองฐานข้อมูล: $backupTool"
}
if (-not (Test-Path $venvPython)) {
    throw "ไม่พบ Python ใน virtual environment: $venvPython"
}

if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupPath = Join-Path $backupDir "factory_stock_$timestamp.db"

& $venvPython $backupTool --source $dbPath --destination $backupPath
if ($LASTEXITCODE -ne 0) {
    throw "สำรองฐานข้อมูลไม่สำเร็จ (Exit code $LASTEXITCODE)"
}
Write-Host "สำรองฐานข้อมูลเรียบร้อย: $backupPath" -ForegroundColor Green

$oldBackups = Get-ChildItem -Path $backupDir -Filter 'factory_stock_*.db' | Sort-Object LastWriteTime -Descending
if ($oldBackups.Count -gt $KeepLatest) {
    $toDelete = $oldBackups | Select-Object -Skip $KeepLatest
    foreach ($file in $toDelete) {
        Remove-Item -Path $file.FullName -Force
    }
    Write-Host "ลบ backup เก่าแล้ว: $($toDelete.Count) ไฟล์" -ForegroundColor Yellow
}
