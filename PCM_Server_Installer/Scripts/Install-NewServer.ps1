param(
    [string]$TargetDirectory = 'C:\PCM',
    [int]$ListenPort = 5000
)

$ErrorActionPreference = 'Stop'
$installerRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$payloadRoot = Join-Path $installerRoot 'Payload\PCM'
$pythonInstaller = Get-ChildItem (Join-Path $installerRoot 'Prerequisites\Python') -Filter 'python-3.12*-amd64.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
$wheelDirectory = Join-Path $installerRoot 'Wheels'

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Python312 {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (& $launcher.Source -3.12 -c "import sys; print(sys.executable)").Trim()
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($version.Trim() -eq '3.12') { return $python.Source }
    }
    return $null
}

if (-not (Test-IsAdministrator)) {
    throw 'กรุณารันตัวติดตั้งด้วยสิทธิ์ Administrator'
}
if (-not (Test-Path (Join-Path $payloadRoot 'my_factory_app\app.py'))) {
    throw "ไม่พบ Payload ของ PCM กรุณารัน BUILD_INSTALLER_PACKAGE.ps1 ที่เครื่องพัฒนาก่อน: $payloadRoot"
}

Write-Step 'ตรวจสอบ Python 3.12'
$pythonCommand = Get-Python312
if (-not $pythonCommand) {
    if ($pythonInstaller) {
        Write-Host "กำลังติดตั้ง $($pythonInstaller.Name)" -ForegroundColor Yellow
        $process = Start-Process -FilePath $pythonInstaller.FullName -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0' -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "ติดตั้ง Python ไม่สำเร็จ (Exit code $($process.ExitCode))" }
    }
    else {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw 'ไม่พบ Python 3.12, ตัวติดตั้ง Offline หรือ winget กรุณาวาง Python installer ใน Prerequisites\Python'
        }
        Write-Host 'ไม่พบตัวติดตั้ง Offline กำลังติดตั้ง Python 3.12 ผ่าน winget' -ForegroundColor Yellow
        & $winget.Source install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "ติดตั้ง Python ผ่าน winget ไม่สำเร็จ (Exit code $LASTEXITCODE)" }
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $pythonCommand = Get-Python312
    if (-not $pythonCommand) { throw 'ติดตั้ง Python แล้วแต่ยังไม่พบ Python 3.12' }
}

Write-Step "ติดตั้งไฟล์ PCM ไปยัง $TargetDirectory"
New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
$robocopyArguments = @(
    $payloadRoot, $TargetDirectory, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS',
    '/XF', 'factory_stock.db', 'factory_stock.db-wal', 'factory_stock.db-shm', '.env', '.secret_key'
)
& robocopy @robocopyArguments | Out-Null
if ($LASTEXITCODE -gt 7) { throw "คัดลอก Payload ไม่สำเร็จ (Robocopy code $LASTEXITCODE)" }

$appDirectory = Join-Path $TargetDirectory 'my_factory_app'
$venvDirectory = Join-Path $TargetDirectory '.venv'
$venvPython = Join-Path $venvDirectory 'Scripts\python.exe'
$requirementsFile = Join-Path $TargetDirectory 'requirements.txt'

Write-Step 'สร้าง Python virtual environment'
if (-not (Test-Path $venvPython)) {
    & $pythonCommand -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) { throw 'สร้าง virtual environment ไม่สำเร็จ' }
}

Write-Step 'ติดตั้ง Python dependencies'
$offlineWheels = Get-ChildItem $wheelDirectory -Filter '*.whl' -File -ErrorAction SilentlyContinue
if ($offlineWheels) {
    & $venvPython -m pip install --no-index --find-links $wheelDirectory -r $requirementsFile
}
else {
    Write-Host 'ไม่พบ Offline Wheels จะติดตั้งผ่านอินเทอร์เน็ต' -ForegroundColor Yellow
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $requirementsFile
}
if ($LASTEXITCODE -ne 0) { throw 'ติดตั้ง Python dependencies ไม่สำเร็จ' }

Write-Step 'สร้างค่าความปลอดภัยของ Server'
$envTarget = Join-Path $appDirectory '.env'
$envExample = Join-Path $appDirectory '.env.server.example'
if (-not (Test-Path $envTarget)) {
    $secretBytes = [byte[]]::new(48)
    [Security.Cryptography.RandomNumberGenerator]::Fill($secretBytes)
    $secret = [Convert]::ToBase64String($secretBytes)
    $envContent = Get-Content $envExample -Raw
    $envContent = $envContent.Replace('CHANGE_THIS_TO_A_LONG_RANDOM_SECRET', $secret)
    Set-Content -LiteralPath $envTarget -Value $envContent -Encoding UTF8
}

Write-Step "ตั้งค่า Firewall พอร์ต $ListenPort"
$firewallRuleName = "PCM Web Server Port $ListenPort"
if (-not (Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ListenPort | Out-Null
}

Write-Step 'สร้าง Scheduled Tasks'
$waitressServer = Join-Path $appDirectory 'waitress_server.py'
$backupScript = Join-Path $TargetDirectory 'backup_factory_db.ps1'
$watchdogScript = Join-Path $TargetDirectory 'watchdog.ps1'
$startupTaskName = 'PCM-Web-Server-Startup'
$backupTaskName = 'PCM-Database-Backup-Daily'
$watchdogTaskName = 'PCM-Web-Server-Watchdog'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest -LogonType ServiceAccount

$startupAction = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$waitressServer`" --host 0.0.0.0 --port $ListenPort --threads 12" -WorkingDirectory $appDirectory
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$startupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $startupTaskName -Action $startupAction -Trigger $startupTrigger -Principal $principal -Settings $startupSettings -Force | Out-Null

$backupAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`""
$backupTrigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $backupTaskName -Action $backupAction -Trigger $backupTrigger -Principal $principal -Settings $backupSettings -Force | Out-Null

$watchdogAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`" -HealthUrl `"http://127.0.0.1:$ListenPort/healthz`""
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $watchdogTaskName -Action $watchdogAction -Trigger $watchdogTrigger -Principal $principal -Settings $watchdogSettings -Force | Out-Null

Write-Step 'สร้าง Shortcut บน Desktop'
$desktopPath = [Environment]::GetFolderPath('CommonDesktopDirectory')
$shortcutPath = Join-Path $desktopPath 'PCM Stock.url'
Set-Content -LiteralPath $shortcutPath -Value "[InternetShortcut]`r`nURL=http://localhost:$ListenPort`r`n" -Encoding ASCII

Write-Step 'ตรวจสอบโค้ดและเปิด Server'
Push-Location $appDirectory
try {
    & $venvPython -m py_compile app.py unit_conversion.py mu_module.py waitress_server.py
    if ($LASTEXITCODE -ne 0) { throw 'ตรวจสอบ Python source ไม่ผ่าน' }
}
finally { Pop-Location }

Start-ScheduledTask -TaskName $startupTaskName
Start-Sleep -Seconds 12
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$ListenPort/healthz" -TimeoutSec 10
if ($health.status -ne 'ok') { throw 'Server เปิดแล้วแต่ health check ไม่ผ่าน' }
Start-ScheduledTask -TaskName $watchdogTaskName

Write-Host "`nติดตั้ง PCM Server สำเร็จ: http://127.0.0.1:$ListenPort" -ForegroundColor Green
Write-Host 'หากต้องการข้อมูลเดิม ให้รัน 02_RESTORE_SERVER.bat ต่อทันที' -ForegroundColor Yellow
