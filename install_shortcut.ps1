# install_shortcut.ps1
$targetUrl    = "http://192.168.2.102:5000"
$shortcutName = "PCM Stock"
$desktop      = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "$shortcutName.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($shortcutPath)
$sc.TargetPath   = "$env:SystemRoot\system32\rundll32.exe"
$sc.Arguments    = "url.dll,FileProtocolHandler $targetUrl"
$sc.Description  = "PCM Stock System"
$sc.IconLocation = "$env:SystemRoot\system32\shell32.dll, 14"
$sc.Save()

Write-Host ""
Write-Host "[OK] Shortcut created: $shortcutPath" -ForegroundColor Green
Write-Host "[OK] Double-click 'PCM Stock' on Desktop to open the system." -ForegroundColor Cyan
Write-Host ""
