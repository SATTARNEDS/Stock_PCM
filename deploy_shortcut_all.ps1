#Requires -RunAsAdministrator
<#
.SYNOPSIS
    กระจาย shortcut PCM Stock ไปยัง Desktop ของทุกเครื่องบน LAN
    รันจากเครื่อง Server โดยไม่รบกวน user ที่กำลังทำงาน

.USAGE
    powershell -ExecutionPolicy Bypass -File .\deploy_shortcut_all.ps1

    # ระบุ subnet เอง
    powershell -ExecutionPolicy Bypass -File .\deploy_shortcut_all.ps1 -Subnet "192.168.1"

    # ระบุ IP เฉพาะเครื่อง
    powershell -ExecutionPolicy Bypass -File .\deploy_shortcut_all.ps1 -TargetIPs "192.168.2.10","192.168.2.11"
#>

param(
    [string]$Subnet       = "192.168.2",
    [string[]]$TargetIPs  = @(),
    [string]$TargetUrl    = "http://192.168.2.102:5000",
    [string]$ShortcutName = "PCM Stock"
)

$ErrorActionPreference = 'SilentlyContinue'

# ============================================================
# สร้าง shortcut content (.lnk) เป็น base64 เพื่อ copy ข้ามเครื่อง
# ใช้วิธี copy ไฟล์ .url ไปวางที่ Desktop แทน (ง่ายและน่าเชื่อถือกว่า)
# ============================================================

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function New-UrlShortcutContent {
    param($Url, $IconPath, $IconIndex)
    return "[InternetShortcut]`r`nURL=$Url`r`nIconFile=$IconPath`r`nIconIndex=$IconIndex`r`n"
}

# สร้าง .url content
$iconPath  = "%SystemRoot%\system32\shell32.dll"
$iconIndex = 14
$urlContent = New-UrlShortcutContent -Url $TargetUrl -IconPath $iconPath -IconIndex $iconIndex

# สร้างไฟล์ temp ที่จะ copy ไป
$tempUrl = Join-Path $env:TEMP "$ShortcutName.url"
Set-Content -Path $tempUrl -Value $urlContent -Encoding ASCII

# ============================================================
# หาเป้าหมาย
# ============================================================
Write-Step "เตรียมรายการเครื่องเป้าหมาย"

if ($TargetIPs.Count -gt 0) {
    $targets = $TargetIPs
    Write-Host "  ใช้ IP ที่ระบุ: $($targets -join ', ')" -ForegroundColor Yellow
} else {
    Write-Host "  สแกนหาเครื่องออนไลน์ใน $Subnet.1 - $Subnet.254 ..." -ForegroundColor Yellow
    $targets = 1..254 | ForEach-Object -Parallel {
        $ip = "$using:Subnet.$_"
        if (Test-Connection -ComputerName $ip -Count 1 -TimeoutSeconds 1 -Quiet) { $ip }
    } -ThrottleLimit 50
    Write-Host "  พบ $($targets.Count) เครื่องออนไลน์" -ForegroundColor Green
}

# ข้าม server ตัวเอง
$serverIPs = (Get-NetIPAddress -AddressFamily IPv4).IPAddress
$targets = $targets | Where-Object { $_ -notin $serverIPs }

if ($targets.Count -eq 0) {
    Write-Host "  ไม่พบเครื่องเป้าหมาย" -ForegroundColor Red
    exit
}

# ============================================================
# กระจาย shortcut ไปแต่ละเครื่อง
# ============================================================
Write-Step "กระจาย shortcut ไปยัง $($targets.Count) เครื่อง"

$success = 0
$failed  = 0
$skipped = 0

foreach ($ip in $targets) {
    $adminShare = "\\$ip\C$"

    # ตรวจว่า admin share เข้าถึงได้ไหม
    if (-not (Test-Path $adminShare)) {
        Write-Host "  [$ip] SKIP - เข้า Admin Share ไม่ได้" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    # หา Desktop ของ user ทุกคนบนเครื่องนั้น
    $usersRoot = "\\$ip\C$\Users"
    if (-not (Test-Path $usersRoot)) {
        Write-Host "  [$ip] SKIP - ไม่พบ C:\Users" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    $deployed = 0
    Get-ChildItem -Path $usersRoot -Directory | Where-Object {
        $_.Name -notin @('Public', 'Default', 'Default User', 'All Users')
    } | ForEach-Object {
        $desktopPath = Join-Path $_.FullName "Desktop"
        if (Test-Path $desktopPath) {
            $dest = Join-Path $desktopPath "$ShortcutName.url"
            Copy-Item -Path $tempUrl -Destination $dest -Force
            $deployed++
        }
    }

    # Desktop ของ Public (ทุก user เห็น)
    $publicDesktop = "\\$ip\C$\Users\Public\Desktop"
    if (Test-Path $publicDesktop) {
        $dest = Join-Path $publicDesktop "$ShortcutName.url"
        Copy-Item -Path $tempUrl -Destination $dest -Force
        $deployed++
    }

    if ($deployed -gt 0) {
        Write-Host "  [$ip] OK - วาง shortcut ใน $deployed Desktop" -ForegroundColor Green
        $success++
    } else {
        Write-Host "  [$ip] SKIP - ไม่พบ Desktop folder" -ForegroundColor DarkGray
        $skipped++
    }
}

# ============================================================
# สรุปผล
# ============================================================
Write-Step "สรุปผล"
Write-Host "  สำเร็จ  : $success เครื่อง" -ForegroundColor Green
Write-Host "  ข้าม    : $skipped เครื่อง" -ForegroundColor DarkGray
Write-Host "  ล้มเหลว : $failed เครื่อง" -ForegroundColor $(if ($failed -gt 0) { 'Red' } else { 'DarkGray' })
Write-Host ""
Write-Host "  Shortcut '$ShortcutName' -> $TargetUrl" -ForegroundColor Cyan
Write-Host "  Icon: globe (shell32.dll #$iconIndex)" -ForegroundColor Cyan

# ลบ temp file
Remove-Item $tempUrl -Force
