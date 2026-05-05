# install_shortcut.ps1
$targetUrl    = "http://192.168.2.102:5000"
$shortcutName = "PCM Stock"
$desktop      = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "$shortcutName.url"

"[InternetShortcut]`r`nURL=$targetUrl`r`n" | Set-Content -Path $shortcutPath -Encoding ASCII

Write-Host ""
Write-Host "[OK] Shortcut created: $shortcutPath" -ForegroundColor Green
Write-Host "[OK] Double-click 'PCM Stock' on Desktop to open the system." -ForegroundColor Cyan
Write-Host ""
