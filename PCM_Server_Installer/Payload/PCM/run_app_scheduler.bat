@echo off
REM Batch wrapper for Task Scheduler to run Flask app
REM บันทึกผลลัพธ์ไปที่ log file

setlocal enabledelayedexpansion

set "APP_ROOT=C:\Users\sattarned\Documents\GitHub\Stock_PCM"
set "LOG_FILE=%APP_ROOT%\app_startup.log"
set "VENV_PATH=%APP_ROOT%\.venv\Scripts"
set "PYTHON_SCRIPT=%APP_ROOT%\my_factory_app\app.py"

REM Function to log messages
goto :start

:log_message
setlocal enabledelayedexpansion
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c-%%a-%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a:%%b)
echo [%mydate% %mytime%] %~1 >> "%LOG_FILE%"
echo [%mydate% %mytime%] %~1
endlocal
exit /b

:start
echo. >> "%LOG_FILE%"
call :log_message "=== Flask App Startup Started (Batch) ==="
call :log_message "App Root: %APP_ROOT%"

REM Check if venv exists
if not exist "%VENV_PATH%\activate.bat" (
    call :log_message "ERROR: Virtual environment not found at %VENV_PATH%"
    exit /b 1
)
call :log_message "Virtual environment found"

REM Change to app directory
cd /d "%APP_ROOT%"
call :log_message "Working directory: %CD%"

REM Check if app.py exists
if not exist "%PYTHON_SCRIPT%" (
    call :log_message "ERROR: app.py not found at %PYTHON_SCRIPT%"
    exit /b 1
)
call :log_message "app.py found"

REM Activate venv
call :log_message "Activating virtual environment..."
call "%VENV_PATH%\activate.bat"
if errorlevel 1 (
    call :log_message "ERROR: Failed to activate virtual environment"
    exit /b 1
)
call :log_message "Virtual environment activated"

REM Set environment variables
call :log_message "Setting environment variables..."
set "FLASK_APP=my_factory_app/app.py"
set "FLASK_DEBUG=0"
set "FLASK_ENV=production"

call :log_message "FLASK_APP=%FLASK_APP%"
call :log_message "FLASK_DEBUG=%FLASK_DEBUG%"
call :log_message "FLASK_ENV=%FLASK_ENV%"

REM Start Flask app
call :log_message "Starting Flask application..."
call :log_message "Running: python %PYTHON_SCRIPT%"

python "%PYTHON_SCRIPT%" 2>&1 >> "%LOG_FILE%"

call :log_message "Flask application exited with code: %ERRORLEVEL%"
exit /b %ERRORLEVEL%
