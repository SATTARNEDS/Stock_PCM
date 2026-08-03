param(
    [switch]$IncludeOfflineWheels
)

$ErrorActionPreference = 'Stop'
$installerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $installerRoot
$payloadRoot = Join-Path $installerRoot 'Payload\PCM'
$workspaceRoot = (Resolve-Path -LiteralPath $repoRoot).Path.TrimEnd('\')
$resolvedInstallerRoot = (Resolve-Path -LiteralPath $installerRoot).Path.TrimEnd('\')

if (-not $resolvedInstallerRoot.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "ตำแหน่ง Installer ไม่อยู่ภายในโปรเจกต์: $resolvedInstallerRoot"
}
if ($payloadRoot -ne (Join-Path $resolvedInstallerRoot 'Payload\PCM')) {
    throw "ตำแหน่ง Payload ไม่ถูกต้อง: $payloadRoot"
}

Write-Host 'กำลังสร้าง Payload โดยไม่รวมฐานข้อมูลและความลับ...' -ForegroundColor Cyan
if (Test-Path $payloadRoot) {
    Remove-Item -LiteralPath $payloadRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

$excludedDirectories = @(
    '.git', '.venv', '.agents', '.codex', '.vscode', '.pytest_cache', '__pycache__', 'logs',
    'PCM_Server_Installer', 'backups', 'my_factory_app\backups', 'my_factory_app\__pycache__'
)
$robocopyArguments = @($repoRoot, $payloadRoot, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS')
$robocopyArguments += '/XD'
$robocopyArguments += $excludedDirectories | ForEach-Object { Join-Path $repoRoot $_ }
$robocopyArguments += @(
    '/XF', '*.db', '*.db-wal', '*.db-shm', '*.partial', '.env', '.secret_key',
    '*.pyc', '*.log', '*.zip', 'test_*.py', 'check_*.py', 'debug_*.py'
)
& robocopy @robocopyArguments | Out-Null
if ($LASTEXITCODE -gt 7) { throw "สร้าง Payload ไม่สำเร็จ (Robocopy code $LASTEXITCODE)" }

if ($IncludeOfflineWheels) {
    $wheelDirectory = Join-Path $installerRoot 'Wheels'
    New-Item -ItemType Directory -Path $wheelDirectory -Force | Out-Null
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $python) { throw 'ไม่พบ py.exe สำหรับดาวน์โหลด Offline Wheels' }
    & $python.Source -3.12 -m pip download -r (Join-Path $repoRoot 'requirements.txt') -d $wheelDirectory
    if ($LASTEXITCODE -ne 0) { throw 'ดาวน์โหลด Offline Wheels ไม่สำเร็จ' }
}

$manifestPath = Join-Path $installerRoot 'SHA256SUMS.txt'
$manifestLines = Get-ChildItem $installerRoot -Recurse -File |
    Where-Object { $_.FullName -ne $manifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($installerRoot.Length + 1)
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        "$hash  $relative"
    }
Set-Content -LiteralPath $manifestPath -Value $manifestLines -Encoding UTF8

Write-Host "สร้าง Installer Package สำเร็จ: $installerRoot" -ForegroundColor Green
Write-Host 'ตรวจแล้วว่า Payload ไม่มีไฟล์ .db, .env หรือ .secret_key' -ForegroundColor Green
