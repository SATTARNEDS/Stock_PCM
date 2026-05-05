@echo off
set "SHORTCUT=%USERPROFILE%\Desktop\PCM Stock.url"
(
  echo [InternetShortcut]
  echo URL=http://192.168.2.102:5000
  echo IconFile=%SystemRoot%\system32\shell32.dll
  echo IconIndex=14
) > "%SHORTCUT%"
echo.
echo [OK] Shortcut created on Desktop: PCM Stock
echo [OK] Double-click "PCM Stock" on Desktop to open the system.
echo.
pause
