param(
    [Parameter(Mandatory = $true)]
    [string]$AuthBaseUrl,
    [string]$PythonExe = "",
    [string]$Version = "1.0.0",
    [DateTimeOffset]$ExpiresAfter = "2026-12-02T00:00:00+08:00",
    [string]$DistPath = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot
if (-not $DistPath) { $DistPath = Join-Path $projectRoot "dist" }
elseif (-not [IO.Path]::IsPathRooted($DistPath)) { $DistPath = Join-Path $projectRoot $DistPath }
$DistPath = [IO.Path]::GetFullPath($DistPath)

$uri = [Uri]$AuthBaseUrl
$isLoopback = $uri.Host -in @("127.0.0.1", "localhost", "::1")
if ($uri.Scheme -ne "https" -and -not $isLoopback) {
    throw "The production authentication server must use HTTPS."
}

if (-not $PythonExe) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if (-not $candidate) { $candidate = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $candidate) {
        throw "Python was not found. Install 64-bit Python or pass -PythonExe."
    }
    $PythonExe = $candidate.Source
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python does not exist: $PythonExe"
}

$releaseDir = Join-Path $projectRoot "build\release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$manifestPath = Join-Path $releaseDir "release_manifest.json"
$manifest = [ordered]@{
    channel = "release"
    version = $Version
    expires_at = $ExpiresAfter.ToString("yyyy-MM-ddTHH:mm:sszzz")
    require_login = $true
    auth_base_url = $AuthBaseUrl.TrimEnd("/")
    session_check_minutes = 10
}
$manifestJson = $manifest | ConvertTo-Json
[IO.File]::WriteAllText($manifestPath, $manifestJson, (New-Object Text.UTF8Encoding($false)))

if (-not $SkipTests) {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = "$projectRoot;$projectRoot\server"
    try {
        & $PythonExe -m pytest -q --basetemp (Join-Path $projectRoot "build\pytest-temp")
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; release build stopped." }
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }
}

$env:CEP_RELEASE_MANIFEST = $manifestPath
try {
    & $PythonExe -m PyInstaller --clean --noconfirm --distpath $DistPath CreativeEnginePro.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}
finally {
    Remove-Item Env:CEP_RELEASE_MANIFEST -ErrorAction SilentlyContinue
}

$exe = Join-Path $DistPath "CreativeEnginePro.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Build completed without expected EXE: $exe" }

# Launch the actual frozen EXE and construct the complete main window.  Merely
# observing a live process is insufficient because a PyInstaller exception
# dialog also leaves the process alive.
$smokeMarker = Join-Path $releaseDir "bundle-smoke.json"
Remove-Item -LiteralPath $smokeMarker -Force -ErrorAction SilentlyContinue
$previousQtPlatform = $env:QT_QPA_PLATFORM
$previousSmokeMarker = $env:CEP_BUNDLE_SMOKE_MARKER
$env:QT_QPA_PLATFORM = "offscreen"
$env:CEP_BUNDLE_SMOKE_MARKER = $smokeMarker
try {
    $smokeProcess = Start-Process -FilePath $exe -ArgumentList "--bundle-smoke" -PassThru -WindowStyle Hidden
    if (-not $smokeProcess.WaitForExit(180000)) {
        Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue
        throw "Frozen EXE smoke test timed out before the main window was constructed."
    }
    if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $smokeMarker)) {
        throw "Frozen EXE smoke test failed (exit code $($smokeProcess.ExitCode))."
    }
    $smokeResult = Get-Content -LiteralPath $smokeMarker -Raw | ConvertFrom-Json
    if (-not $smokeResult.ok) { throw "Frozen EXE smoke marker was invalid." }
}
finally {
    if ($null -eq $previousQtPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    else { $env:QT_QPA_PLATFORM = $previousQtPlatform }
    if ($null -eq $previousSmokeMarker) { Remove-Item Env:CEP_BUNDLE_SMOKE_MARKER -ErrorAction SilentlyContinue }
    else { $env:CEP_BUNDLE_SMOKE_MARKER = $previousSmokeMarker }
}

# On Windows, antivirus/indexing and the one-file finalizer can keep the large
# output changing briefly after the builder returns.  Hash only a stable file.
$previousSignature = ""
$stablePasses = 0
for ($attempt = 0; $attempt -lt 30 -and $stablePasses -lt 2; $attempt++) {
    $item = Get-Item -LiteralPath $exe
    $signature = "$($item.Length):$($item.LastWriteTimeUtc.Ticks)"
    if ($signature -eq $previousSignature) { $stablePasses++ }
    else { $stablePasses = 0; $previousSignature = $signature }
    Start-Sleep -Seconds 1
}
if ($stablePasses -lt 2) { throw "Release EXE did not become stable before hashing." }

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $exe
$hashLine = "$($hash.Hash.ToLower())  CreativeEnginePro.exe"
$hashLine | Set-Content -LiteralPath (Join-Path $DistPath "CreativeEnginePro.sha256.txt") -Encoding ascii

Write-Host ""
Write-Host "Release created: $exe" -ForegroundColor Green
Write-Host "Version: $Version"
Write-Host "Expires: $($manifest.expires_at) (valid through December 1)"
Write-Host "SHA256: $($hash.Hash)"
