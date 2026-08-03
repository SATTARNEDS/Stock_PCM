param(
    [string]$TargetDirectory = 'C:\PCM',
    [string]$BackupPath = '',
    [int]$ListenPort = 5000
)

$ErrorActionPreference = 'Stop'
$installerRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$appDirectory = Join-Path $TargetDirectory 'my_factory_app'
$databasePath = Join-Path $appDirectory 'factory_stock.db'
$backupTool = Join-Path $appDirectory 'backup_database.py'
$venvPython = Join-Path $TargetDirectory '.venv\Scripts\python.exe'
$restoreTool = Join-Path $installerRoot 'Scripts\restore_database.py'
$startupTaskName = 'PCM-Web-Server-Startup'
$watchdogTaskName = 'PCM-Web-Server-Watchdog'

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

if (-not (Test-Path $venvPython)) { throw "ยังไม่ได้ติดตั้ง PCM Server ที่ $TargetDirectory" }
if (-not (Test-Path $restoreTool)) { throw "ไม่พบเครื่องมือกู้คืน: $restoreTool" }

if (-not $BackupPath) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = [Windows.Forms.OpenFileDialog]::new()
    $dialog.Title = 'เลือกไฟล์ฐานข้อมูล PCM ที่ต้องการกู้คืน'
    $dialog.Filter = 'SQLite Database (*.db)|*.db'
    $dialog.InitialDirectory = Join-Path $installerRoot 'Database_Backup'
    if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) {
        Write-Host 'ยกเลิกการกู้คืน' -ForegroundColor Yellow
        exit 0
    }
    $BackupPath = $dialog.FileName
}

$sourceBackup = (Resolve-Path -LiteralPath $BackupPath).Path
if ([IO.Path]::GetExtension($sourceBackup) -ne '.db') { throw 'รองรับเฉพาะไฟล์ .db' }
if ($sourceBackup -eq $databasePath) { throw 'ไฟล์ต้นทางและฐานข้อมูลปลายทางเป็นไฟล์เดียวกัน' }

Write-Step 'ตรวจสอบฐานข้อมูลสำรองก่อนหยุด Server'
& $venvPython $restoreTool --verify-only --source $sourceBackup
if ($LASTEXITCODE -ne 0) { throw 'ฐานข้อมูลสำรองไม่ผ่าน integrity_check จึงยกเลิกการกู้คืน' }

$safetyDirectory = Join-Path $appDirectory 'backups'
New-Item -ItemType Directory -Path $safetyDirectory -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$safetyBackup = Join-Path $safetyDirectory "factory_stock_before_restore_$timestamp.db"
if (Test-Path $databasePath) {
    Write-Step 'สำรองฐานข้อมูลปัจจุบันก่อนกู้คืน'
    & $venvPython $backupTool --source $databasePath --destination $safetyBackup
    if ($LASTEXITCODE -ne 0) { throw 'สำรองฐานข้อมูลปัจจุบันไม่สำเร็จ จึงยกเลิกการกู้คืน' }
}

Write-Step 'หยุด Server และ Watchdog'
Stop-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName $startupTaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

try {
    Write-Step 'กู้คืนฐานข้อมูลด้วย SQLite Backup API'
    & $venvPython $restoreTool --source $sourceBackup --destination $databasePath
    if ($LASTEXITCODE -ne 0) { throw 'กู้คืนฐานข้อมูลไม่สำเร็จ' }

    Write-Step 'เปิด Server และตรวจ Health'
    Start-ScheduledTask -TaskName $startupTaskName
    Start-Sleep -Seconds 12
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ListenPort/healthz" -TimeoutSec 10
    if ($health.status -ne 'ok') { throw 'ฐานข้อมูลถูกกู้คืนแล้ว แต่ Server health check ไม่ผ่าน' }
    Start-ScheduledTask -TaskName $watchdogTaskName
}
catch {
    $failureMessage = $_.Exception.Message
    Write-Host $failureMessage -ForegroundColor Red
    if (Test-Path $safetyBackup) {
        Write-Host 'กำลังย้อนกลับไปใช้ฐานข้อมูลก่อนกู้คืน...' -ForegroundColor Yellow
        Stop-ScheduledTask -TaskName $startupTaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        & $venvPython $restoreTool --source $safetyBackup --destination $databasePath
        if ($LASTEXITCODE -eq 0) {
            Start-ScheduledTask -TaskName $startupTaskName
            Start-Sleep -Seconds 12
            try {
                $rollbackHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$ListenPort/healthz" -TimeoutSec 10
                if ($rollbackHealth.status -eq 'ok') {
                    Start-ScheduledTask -TaskName $watchdogTaskName
                    Write-Host 'ย้อนกลับฐานข้อมูลเดิมและเปิด Server สำเร็จ' -ForegroundColor Green
                }
            }
            catch {
                Write-Host 'ย้อนฐานข้อมูลแล้ว แต่ Server ยังไม่ผ่าน health check กรุณาตรวจ logs' -ForegroundColor Red
            }
        }
        Write-Host "ฐานข้อมูลเดิมที่สำรองไว้: $safetyBackup" -ForegroundColor Yellow
    }
    throw $failureMessage
}

Write-Host "`nกู้คืน PCM Server สำเร็จ" -ForegroundColor Green
Write-Host "ฐานข้อมูลที่ใช้: $databasePath" -ForegroundColor Green
if (Test-Path $safetyBackup) { Write-Host "ฐานข้อมูลก่อนกู้คืน: $safetyBackup" -ForegroundColor Yellow }
