param(
    [string]$PythonCommand = "py",
    [switch]$SkipTasks,
    [switch]$SkipFirewall,
    [string]$ListenPort = "5000"
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) {
    Write-Host "`n==> $message" -ForegroundColor Cyan
}

function Test-IsAdmin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $repoRoot 'my_factory_app'
$venvDir = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$requirementsFile = Join-Path $repoRoot 'requirements.txt'
$envExample = Join-Path $appDir '.env.server.example'
$envTarget = Join-Path $appDir '.env'
$startScript = Join-Path $repoRoot 'start.ps1'
$backupScript = Join-Path $repoRoot 'backup_factory_db.ps1'

if (-not (Test-Path $appDir)) {
    throw "ไม่พบโฟลเดอร์ my_factory_app: $appDir"
}
if (-not (Test-Path $requirementsFile)) {
    throw "ไม่พบไฟล์ requirements.txt: $requirementsFile"
}
if (-not (Test-Path $startScript)) {
    throw "ไม่พบไฟล์ start.ps1: $startScript"
}

Write-Step "ตรวจสอบและสร้าง Python virtual environment"
if (-not (Test-Path $venvPython)) {
    if ($PythonCommand -eq 'py') {
        & py -3.12 -m venv $venvDir
    }
    else {
        & $PythonCommand -m venv $venvDir
    }
}

Write-Step "ติดตั้ง dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r $requirementsFile

Write-Step "เตรียมไฟล์ .env"
if (-not (Test-Path $envTarget)) {
    Copy-Item $envExample $envTarget
    Write-Host "สร้างไฟล์ .env แล้ว: $envTarget" -ForegroundColor Yellow
    Write-Host "กรุณาแก้ค่า FLASK_SECRET_KEY / SMTP ในไฟล์ .env ก่อนใช้งานจริง" -ForegroundColor Yellow
}
else {
    Write-Host "พบไฟล์ .env อยู่แล้ว: $envTarget" -ForegroundColor Green
}

Write-Step "ทดสอบ import แอป"
Push-Location $appDir
try {
    & $venvPython -c "import app; print('app import ok')"
}
finally {
    Pop-Location
}

$isAdmin = Test-IsAdmin
if (-not $isAdmin -and (-not $SkipFirewall -or -not $SkipTasks)) {
    Write-Host "ไม่ได้รันด้วยสิทธิ์ Administrator: จะข้ามการตั้งค่า Firewall/Task อัตโนมัติ" -ForegroundColor Yellow
    $SkipFirewall = $true
    $SkipTasks = $true
}

if (-not $SkipFirewall) {
    Write-Step "ตั้งค่า Windows Firewall สำหรับพอร์ต $ListenPort"
    $ruleName = "PCM Web Server Port $ListenPort"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ListenPort | Out-Null
        Write-Host "เพิ่ม firewall rule แล้ว: $ruleName" -ForegroundColor Green
    }
    else {
        Write-Host "มี firewall rule อยู่แล้ว: $ruleName" -ForegroundColor Green
    }
}

if (-not $SkipTasks) {
    Write-Step "สร้าง Scheduled Tasks (Startup + Backup)"

    $startupTaskName = "PCM-Web-Server-Startup"
    $backupTaskName = "PCM-Database-Backup-Daily"

    $startupAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
    $startupTrigger = New-ScheduledTaskTrigger -AtStartup
    $startupPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount
    $startupSettings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask -TaskName $startupTaskName -Action $startupAction -Trigger $startupTrigger -Principal $startupPrincipal -Settings $startupSettings -Force | Out-Null

    $backupAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`""
    $backupTrigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
    $backupPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount
    $backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $backupTaskName -Action $backupAction -Trigger $backupTrigger -Principal $backupPrincipal -Settings $backupSettings -Force | Out-Null

    Write-Host "สร้าง Scheduled Tasks เรียบร้อย" -ForegroundColor Green
}

Write-Step "สร้าง Desktop Shortcut"
$desktopPath = [Environment]::GetFolderPath("CommonDesktopDirectory")
$shortcutUrl = Join-Path $desktopPath "PCM Stock.url"
$urlContent = "[InternetShortcut]`r`nURL=http://localhost:$ListenPort`r`n"
Set-Content -Path $shortcutUrl -Value $urlContent -Encoding ASCII
Write-Host "สร้าง shortcut บน Desktop แล้ว: $shortcutUrl" -ForegroundColor Green

Write-Step "เสร็จสิ้น"
Write-Host "เริ่มรันทันที: powershell -ExecutionPolicy Bypass -File `"$startScript`"" -ForegroundColor Cyan
Write-Host "ทดสอบเข้าเว็บ: http://127.0.0.1:$ListenPort" -ForegroundColor Cyan
Write-Host "ดับเบิลคลิก shortcut `"PCM Stock`" บน Desktop เพื่อเข้าระบบ" -ForegroundColor Cyan
