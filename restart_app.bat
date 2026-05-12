@echo off
REM Restart Flask app - Kill old process and start new one

setlocal enabledelayedexpansion

set "APP_ROOT=C:\Users\sattarned\Documents\GitHub\Stock_PCM"
set "LOG_FILE=%APP_ROOT%\app_restart.log"
set "VENV_PATH=%APP_ROOT%\.venv\Scripts"
set "PYTHON_SCRIPT=%APP_ROOT%\my_factory_app\app.py"
set "PORT=5000"

REM Create log entry
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c-%%a-%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a:%%b)

echo. >> "%LOG_FILE%"
echo [%mydate% %mytime%] ========== Flask App Restart ========== >> "%LOG_FILE%"

REM Kill existing Python process on port 5000
echo [%mydate% %mytime%] Checking for existing processes on port %PORT%... >> "%LOG_FILE%"
echo [%mydate% %mytime%] Checking for existing processes on port %PORT%...

for /f "tokens=5" %%a in ('netstat -ano ^| find ":%PORT%"') do (
    echo [%mydate% %mytime%] Found PID: %%a, terminating... >> "%LOG_FILE%"
    echo [%mydate% %mytime%] Found PID: %%a, terminating...
    taskkill /PID %%a /F 2>nul
)

REM Wait for port to be free
echo [%mydate% %mytime%] Waiting 3 seconds for port to free up... >> "%LOG_FILE%"
timeout /t 3 /nobreak

REM Check disk space
for /f "tokens=3" %%a in ('dir "%APP_ROOT%\.."') do (
    set "diskspace=%%a"
)
echo [%mydate% %mytime%] Disk space OK >> "%LOG_FILE%"

REM Check database integrity
if exist "%APP_ROOT%\my_factory_app\factory_stock.db" (
    echo [%mydate% %mytime%] Database file found >> "%LOG_FILE%"
) else (
    echo [%mydate% %mytime%] WARNING: Database file not found >> "%LOG_FILE%"
)

REM Change to app directory
cd /d "%APP_ROOT%"
echo [%mydate% %mytime%] Working directory: %CD% >> "%LOG_FILE%"

REM Activate venv
call "%VENV_PATH%\activate.bat" 2>>"%LOG_FILE%"

REM Set environment
set "FLASK_APP=my_factory_app/app.py"
set "FLASK_DEBUG=0"
set "FLASK_ENV=production"

echo [%mydate% %mytime%] ========== Starting Flask App ========== >> "%LOG_FILE%"
echo [%mydate% %mytime%] Starting Flask App...

REM Run with output to log
python "%PYTHON_SCRIPT%" 2>&1 >> "%LOG_FILE%"

if errorlevel 1 (
    echo [%mydate% %mytime%] ERROR: Flask app exited with error code %ERRORLEVEL% >> "%LOG_FILE%"
    echo ERROR: Flask app exited! Check %LOG_FILE%
) else (
    echo [%mydate% %mytime%] Flask app exited normally >> "%LOG_FILE%"
)

pause
