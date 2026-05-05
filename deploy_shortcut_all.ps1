#Requires -RunAsAdministrator

param(
    [string]$Subnet = '192.168.2',
    [string[]]$TargetIPs = @(),
    [string]$TargetUrl = 'http://192.168.2.102:5000',
    [string]$ShortcutName = 'PCM Stock',
    [int]$PingTimeoutMs = 150,
    [switch]$PromptCredential
)

$ErrorActionPreference = 'SilentlyContinue'

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function New-ShortcutText([string]$Url) {
    return "[InternetShortcut]`r`nURL=$Url`r`nIconFile=%SystemRoot%\system32\shell32.dll`r`nIconIndex=14`r`n"
}

function New-DriveName([int]$Index) {
    return ('PCM{0}' -f $Index)
}

function Remove-DriveIfExists([string]$Name) {
    $existing = Get-PSDrive -Name $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-PSDrive -Name $Name -Force
    }
}

function Deploy-ToUsersRoot([string]$UsersRootPath, [string]$TempShortcut, [string]$Name) {
    $deployedCount = 0

    $publicDesktop = Join-Path $UsersRootPath 'Public\Desktop'
    if (Test-Path $publicDesktop) {
        Copy-Item -Path $TempShortcut -Destination (Join-Path $publicDesktop ($Name + '.url')) -Force
        $deployedCount++
    }

    Get-ChildItem -Path $UsersRootPath -Directory | Where-Object {
        $_.Name -notin @('Public', 'Default', 'Default User', 'All Users')
    } | ForEach-Object {
        $desktopPath = Join-Path $_.FullName 'Desktop'
        if (Test-Path $desktopPath) {
            Copy-Item -Path $TempShortcut -Destination (Join-Path $desktopPath ($Name + '.url')) -Force
            $deployedCount++
        }
    }

    return $deployedCount
}

Write-Step 'Prepare shortcut file'
$tempUrl = Join-Path $env:TEMP ($ShortcutName + '.url')
Set-Content -Path $tempUrl -Value (New-ShortcutText -Url $TargetUrl) -Encoding ASCII

$credential = $null
if ($PromptCredential) {
    Write-Step 'Prompt for remote admin credential'
    $credential = Get-Credential -Message 'Enter admin credential for client machines (DOMAIN\\user or MACHINE\\user)'
}

Write-Step 'Resolve target IPs'
if ($TargetIPs -and $TargetIPs.Count -gt 0) {
    $targets = @($TargetIPs)
}
else {
    $targets = @()
    $scanTotal = 254
    foreach ($i in 1..$scanTotal) {
        $ip = '{0}.{1}' -f $Subnet, $i

        $percent = [int](($i / $scanTotal) * 100)
        Write-Progress -Activity 'Resolve target IPs' -Status ("Scanning {0} ({1}/{2})" -f $ip, $i, $scanTotal) -PercentComplete $percent

        # ping.exe with explicit timeout is much faster and more predictable on PS 5.1 than Test-Connection.
        $null = & ping.exe -n 1 -w $PingTimeoutMs $ip
        if ($LASTEXITCODE -eq 0) {
            $targets += $ip
        }
    }
    Write-Progress -Activity 'Resolve target IPs' -Completed
}

$serverIps = @((Get-NetIPAddress -AddressFamily IPv4).IPAddress)
$targets = @($targets | Where-Object { $_ -and ($_ -notin $serverIps) } | Select-Object -Unique)

if (-not $targets -or $targets.Count -eq 0) {
    Write-Host 'No target machines found.' -ForegroundColor Yellow
    Remove-Item $tempUrl -Force
    exit 0
}

Write-Step ('Deploy shortcut to {0} machine(s)' -f $targets.Count)
$success = 0
$failed = 0
$skipped = 0

foreach ($ip in $targets) {
    $deployed = 0
    $usersRoot = '\\' + $ip + '\C$\Users'

    if ($credential) {
        $driveName = New-DriveName -Index ($success + $failed + $skipped + 1)
        Remove-DriveIfExists -Name $driveName
        try {
            $null = New-PSDrive -Name $driveName -PSProvider FileSystem -Root ('\\' + $ip + '\C$') -Credential $credential -Scope Script
            $usersRoot = $driveName + ':\Users'
        }
        catch {
            Write-Host ('  [{0}] FAIL - Cannot access admin share with provided credential' -f $ip) -ForegroundColor Red
            $failed++
            continue
        }
    }

    if (-not (Test-Path $usersRoot)) {
        Write-Host ('  [{0}] FAIL - Cannot access admin share' -f $ip) -ForegroundColor Red
        $failed++
        if ($credential) {
            Remove-DriveIfExists -Name $driveName
        }
        continue
    }

    try {
        $deployed = Deploy-ToUsersRoot -UsersRootPath $usersRoot -TempShortcut $tempUrl -Name $ShortcutName
    }
    finally {
        if ($credential) {
            Remove-DriveIfExists -Name $driveName
        }
    }

    if ($deployed -gt 0) {
        Write-Host ('  [{0}] OK - deployed to {1} desktop location(s)' -f $ip, $deployed) -ForegroundColor Green
        $success++
    }
    else {
        Write-Host ('  [{0}] SKIP - no desktop folder found' -f $ip) -ForegroundColor DarkGray
        $skipped++
    }
}

Write-Step 'Summary'
Write-Host ('  Success: {0}' -f $success) -ForegroundColor Green
Write-Host ('  Skipped: {0}' -f $skipped) -ForegroundColor DarkGray
if ($failed -gt 0) {
    Write-Host ('  Failed : {0}' -f $failed) -ForegroundColor Red
}
else {
    Write-Host ('  Failed : {0}' -f $failed) -ForegroundColor DarkGray
}

Write-Host ('  Shortcut: {0} -> {1}' -f $ShortcutName, $TargetUrl) -ForegroundColor Cyan

Remove-Item $tempUrl -Force
