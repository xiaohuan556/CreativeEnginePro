param(
    [Parameter(Mandatory = $true)]
    [string]$AuthBaseUrl,
    [string]$PythonExe = "",
    [string]$Version = "1.0.0",
    [DateTimeOffset]$ExpiresAfter = "2026-12-02T00:00:00+08:00",
    [string]$DistPath = "",
    [switch]$AllowInsecureLan,
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
$parsedAddress = $null
$isPrivateIpv4 = [Net.IPAddress]::TryParse($uri.Host, [ref]$parsedAddress) -and
    $parsedAddress.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
    (($parsedAddress.GetAddressBytes()[0] -eq 10) -or
     ($parsedAddress.GetAddressBytes()[0] -eq 172 -and $parsedAddress.GetAddressBytes()[1] -ge 16 -and $parsedAddress.GetAddressBytes()[1] -le 31) -or
     ($parsedAddress.GetAddressBytes()[0] -eq 192 -and $parsedAddress.GetAddressBytes()[1] -eq 168))
$allowedLanHttp = $AllowInsecureLan -and $uri.Scheme -eq "http" -and $isPrivateIpv4
if ($uri.Scheme -ne "https" -and -not $isLoopback -and -not $allowedLanHttp) {
    throw "The production authentication server must use HTTPS."
}
if ($allowedLanHttp) {
    Write-Warning "Building for trusted LAN only. Account traffic is not encrypted and must not be exposed to the Internet."
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
$safeBuildVersion = ($Version -replace '[^A-Za-z0-9._-]', '_')
$pyInstallerWorkPath = Join-Path $projectRoot "build\pyinstaller-$safeBuildVersion"
$manifestPath = Join-Path $releaseDir "release_manifest.json"
$manifest = [ordered]@{
    channel = "release"
    version = $Version
    expires_at = $ExpiresAfter.ToString("yyyy-MM-ddTHH:mm:sszzz")
    require_login = $true
    auth_base_url = $AuthBaseUrl.TrimEnd("/")
    session_check_minutes = 10
    allow_insecure_lan = [bool]$allowedLanHttp
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
    # Keep each release's intermediate PKG separate. A running one-file build,
    # antivirus, or Windows Search can temporarily lock an older PKG and should
    # not prevent a new release from being produced.
    & $PythonExe -m PyInstaller --clean --noconfirm --workpath $pyInstallerWorkPath --distpath $DistPath CreativeEnginePro.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}
finally {
    Remove-Item Env:CEP_RELEASE_MANIFEST -ErrorAction SilentlyContinue
}

# Guard against PyInstaller producing a default 200x200 window titled "tk".
# Font family names with spaces are written into Tcl without quoting by some
# PyInstaller versions and abort the splash script before it becomes branded.
$splashScript = Join-Path $pyInstallerWorkPath "CreativeEnginePro\Splash-00_script.tcl"
if (-not (Test-Path -LiteralPath $splashScript)) {
    throw "Startup splash script was not generated."
}
$splashSource = Get-Content -LiteralPath $splashScript -Raw
if ($splashSource -notmatch "font actual TkDefaultFont" -or
        $splashSource -notmatch "wm overrideredirect \. 1" -or
        $splashSource -notmatch "progress_fill" -or
        $splashSource -notmatch "__CEP_READY__" -or
        $splashSource -notmatch "splash_primary_center" -or
        $splashSource -match "winfo pointerx" -or
        $splashSource -match 'itemconfigure \$tag -text \$var') {
    throw "Startup splash validation failed; refusing to publish a blank Tk window."
}

$exe = Join-Path $DistPath "CreativeEnginePro.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Build completed without expected EXE: $exe" }

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutMilliseconds = 60000
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    # Windows PowerShell 5.1 lacks ProcessStartInfo.ArgumentList.  The worker
    # smoke arguments are fixed switches, but quote them correctly regardless.
    $startInfo.Arguments = (($Arguments | ForEach-Object {
        '"' + $_.Replace('"', '\"') + '"'
    }) -join ' ')

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Failed to start frozen worker smoke test." }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        throw "Frozen worker smoke test timed out."
    }
    # Complete asynchronous pipe drains before reading results and ExitCode.
    $process.WaitForExit()
    return [PSCustomObject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdoutTask.Result
        Stderr = $stderrTask.Result
    }
}

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
        # One-file PyInstaller launches a child process. Kill the tree while
        # the parent still exists so a failed probe cannot leave a hidden EXE.
        & taskkill.exe /PID $smokeProcess.Id /T /F 2>$null | Out-Null
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

# Verify that the frozen executable can dispatch its bundled yt-dlp without
# falling through to the normal desktop login flow.  This is a separate smoke
# test because sys.executable points back to CreativeEnginePro.exe in one-file
# builds.
$ytdlpStdout = Join-Path $releaseDir "ytdlp-worker-smoke.stdout.txt"
$ytdlpStderr = Join-Path $releaseDir "ytdlp-worker-smoke.stderr.txt"
Remove-Item -LiteralPath $ytdlpStdout,$ytdlpStderr -Force -ErrorAction SilentlyContinue
$ytdlpProcess = Invoke-CapturedProcess -FilePath $exe -Arguments @("--ytdlp-worker", "--version")
[IO.File]::WriteAllText($ytdlpStdout, $ytdlpProcess.Stdout, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText($ytdlpStderr, $ytdlpProcess.Stderr, (New-Object Text.UTF8Encoding($false)))
$ytdlpVersion = $ytdlpProcess.Stdout.Trim()
if ($ytdlpProcess.ExitCode -ne 0 -or -not $ytdlpVersion) {
    throw "Frozen yt-dlp worker smoke test failed (exit code $($ytdlpProcess.ExitCode)): $($ytdlpProcess.Stderr)"
}

# TikTok's current web challenge requires curl_cffi-based browser TLS
# impersonation.  A build can import yt-dlp successfully while silently
# omitting curl_cffi's native DLL, so assert the capability on the final EXE.
$impersonateStdout = Join-Path $releaseDir "ytdlp-impersonate-smoke.stdout.txt"
$impersonateStderr = Join-Path $releaseDir "ytdlp-impersonate-smoke.stderr.txt"
Remove-Item -LiteralPath $impersonateStdout,$impersonateStderr -Force -ErrorAction SilentlyContinue
$impersonateProcess = Invoke-CapturedProcess -FilePath $exe -Arguments @("--ytdlp-worker", "--list-impersonate-targets")
[IO.File]::WriteAllText($impersonateStdout, $impersonateProcess.Stdout, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText($impersonateStderr, $impersonateProcess.Stderr, (New-Object Text.UTF8Encoding($false)))
$impersonateOutput = $impersonateProcess.Stdout
if ($impersonateProcess.ExitCode -ne 0 -or $impersonateOutput -notmatch "curl_cffi") {
    throw "Frozen TikTok TLS component smoke test failed (exit code $($impersonateProcess.ExitCode)): $($impersonateProcess.Stderr)"
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
