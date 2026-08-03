# PowerShell script for Task Scheduler to run Flask app reliably
# บันทึกผลลัพธ์ทั้งหมดไปที่ log file เพื่อ debug ได้

$AppRoot = "C:\Users\sattarned\Documents\GitHub\Stock_PCM"
$LogFile = "$AppRoot\app_startup.log"
$VenvPath = "$AppRoot\.venv\Scripts\Activate.ps1"
$PythonScript = "$AppRoot\my_factory_app\app.py"

function Log-Message {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $LogMessage = "[$Timestamp] $Message"
    Write-Host $LogMessage
    Add-Content -Path $LogFile -Value $LogMessage
}

try {
    Log-Message "=== Flask App Startup Started ==="
    Log-Message "App Root: $AppRoot"
    
    # 1. ตรวจสอบ Virtual Environment
    if (-not (Test-Path $VenvPath)) {
        Log-Message "ERROR: Virtual environment not found at $VenvPath"
        exit 1
    }
    Log-Message "Virtual environment found"
    
    # 2. Activate virtual environment
    Log-Message "Activating virtual environment..."
    & $VenvPath
    
    if ($LASTEXITCODE -ne 0) {
        Log-Message "ERROR: Failed to activate virtual environment (Exit code: $LASTEXITCODE)"
        exit 1
    }
    Log-Message "Virtual environment activated successfully"
    
    # 3. เปลี่ยนไปที่ app directory
    Set-Location $AppRoot
    Log-Message "Working directory changed to: $(Get-Location)"
    
    # 4. ตรวจสอบ app.py
    if (-not (Test-Path $PythonScript)) {
        Log-Message "ERROR: app.py not found at $PythonScript"
        exit 1
    }
    Log-Message "app.py found"
    
    # 5. Set environment variables
    Log-Message "Setting environment variables..."
    $env:FLASK_APP = "my_factory_app/app.py"
    $env:FLASK_DEBUG = "0"
    $env:FLASK_ENV = "production"
    
    Log-Message "FLASK_APP: $env:FLASK_APP"
    Log-Message "FLASK_DEBUG: $env:FLASK_DEBUG"
    Log-Message "FLASK_ENV: $env:FLASK_ENV"
    
    # 6. เริ่มต้น app
    Log-Message "Starting Flask application..."
    Log-Message "Python version: $(python --version 2>&1)"
    
    # เรียก python โดยใช้ full path
    $PythonExe = (Get-Command python).Source
    Log-Message "Using Python executable: $PythonExe"
    
    # Run app and redirect output to log
    Log-Message "Launching: $PythonExe $PythonScript"
    & $PythonExe $PythonScript 2>&1 | Tee-Object -FilePath $LogFile -Append
    
    Log-Message "Flask application exited with code: $LASTEXITCODE"
}
catch {
    $ErrorMsg = $_.Exception.Message
    Log-Message "CRITICAL ERROR: $ErrorMsg"
    Log-Message "Stack trace: $($_.ScriptStackTrace)"
    exit 1
}
