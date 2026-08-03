@echo off
setlocal
set "TASK_NAME=PCM-Web-Server-Startup"

echo Restarting PCM Web Server...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Stop-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3; Start-ScheduledTask -TaskName '%TASK_NAME%'"

if errorlevel 1 (
  echo Restart failed. Please run this file as Administrator.
  exit /b 1
)

timeout /t 12 /nobreak >nul
powershell.exe -NoProfile -Command ^
  "try { $r=Invoke-RestMethod 'http://127.0.0.1:5000/healthz' -TimeoutSec 8; if($r.status -eq 'ok'){Write-Host 'PCM Web Server is healthy'; exit 0} }; catch {}; Write-Host 'PCM Web Server is not healthy'; exit 1"
exit /b %errorlevel%
