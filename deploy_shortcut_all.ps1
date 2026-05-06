#Requires -RunAsAdministrator
# Deploy PCM Stock shortcut via net use (SMB) + Copy-Item.
# Usage: .\deploy_shortcut_all.ps1 -TargetIPs "192.168.2.103"
#        .\deploy_shortcut_all.ps1 -Subnet "192.168.2"

param(
    [string]$Subnet       = '192.168.2',
    [string[]]$TargetIPs  = @(),
    [string]$TargetUrl    = 'http://192.168.2.102:5000',
    [string]$ShortcutName = 'PCM Stock',
    [int]$PingTimeoutMs   = 300,
    [string]$AdminUser    = '',
    [string]$AdminPass    = ''
)

# -- Credential --
if (-not $AdminUser) {
    $cred      = Get-Credential -Message 'Enter admin credential for client machines (e.g. .\Administrator)'
    $AdminUser = $cred.UserName
    $AdminPass = $cred.GetNetworkCredential().Password
}

# -- Create shortcut file locally --
$tempFile = Join-Path $env:TEMP ($ShortcutName + '.url')
$content  = "[InternetShortcut]`r`nURL=$TargetUrl`r`nIconFile=%SystemRoot%\system32\shell32.dll`r`nIconIndex=14`r`n"
[System.IO.File]::WriteAllText($tempFile, $content, [System.Text.Encoding]::ASCII)

# -- Resolve targets --
if ($TargetIPs -and $TargetIPs.Count -gt 0) {
    $targets = @($TargetIPs)
} else {
    Write-Host ("`n==> Scanning {0}.1-254 ..." -f $Subnet) -ForegroundColor Cyan
    $targets = @()
    for ($i = 1; $i -le 254; $i++) {
        $ip   = ('{0}.{1}' -f $Subnet, $i)
        $perc = [int](($i / 254) * 100)
        Write-Progress -Activity 'Ping scan' -Status ('{0} ({1}/254)' -f $ip, $i) -PercentComplete $perc
        $null = & ping.exe -n 1 -w $PingTimeoutMs $ip
        if ($LASTEXITCODE -eq 0) { $targets += $ip }
    }
    Write-Progress -Activity 'Ping scan' -Completed
}

$serverIps = @((Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress)
$targets   = @($targets | Where-Object { $_ -and ($_ -notin $serverIps) } | Select-Object -Unique)

if ($targets.Count -eq 0) {
    Write-Host 'No responsive machines found.' -ForegroundColor Yellow
    Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    exit 0
}

# -- Deploy --
Write-Host ("`n==> Deploying to {0} machine(s)" -f $targets.Count) -ForegroundColor Cyan
$success = 0
$failed  = 0
$idx     = 0

foreach ($ip in $targets) {
    $idx++
    Write-Progress -Activity 'Deploy' -Status ('{0} ({1}/{2})' -f $ip, $idx, $targets.Count) -PercentComplete ([int]($idx / $targets.Count * 100))
    Write-Host ('  [{0}] ' -f $ip) -NoNewline

    $share = '\\' + $ip + '\C$'

    # 1) Connect via net use with explicit credential
    $null = & net.exe use $share /delete /y 2>&1   # clear stale session first
    $connectOut = & net.exe use $share $AdminPass /user:$AdminUser 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ('FAIL (connect) - ' + ($connectOut -join ' ')) -ForegroundColor Red
        $failed++
        continue
    }

    # 2) Copy shortcut to Public Desktop
    $dest = $share + '\Users\Public\Desktop\' + $ShortcutName + '.url'
    try {
        Copy-Item -Path $tempFile -Destination $dest -Force -ErrorAction Stop
        Write-Host 'OK - shortcut placed on Public Desktop' -ForegroundColor Green
        $success++
    } catch {
        Write-Host ('FAIL (copy) - ' + $_.Exception.Message) -ForegroundColor Red
        $failed++
    }

    # 3) Disconnect
    $null = & net.exe use $share /delete /y 2>&1
}

Write-Progress -Activity 'Deploy' -Completed
Remove-Item $tempFile -Force -ErrorAction SilentlyContinue

# -- Summary --
Write-Host "`n==> Summary"
Write-Host ('  Success: {0}' -f $success) -ForegroundColor Green
$fc = if ($failed -gt 0) { 'Red' } else { 'DarkGray' }
Write-Host ('  Failed : {0}' -f $failed) -ForegroundColor $fc
Write-Host ('  URL    : {0}' -f $TargetUrl) -ForegroundColor Cyan

