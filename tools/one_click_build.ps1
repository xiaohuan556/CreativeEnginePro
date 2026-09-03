param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configPath = Join-Path $projectRoot "packaging.config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    # Keep the user-facing filename in one place; resolving by wildcard avoids
    # Windows PowerShell 5.1 source-encoding problems with non-ASCII literals.
    $configPath = (Get-ChildItem -LiteralPath $projectRoot -Filter "*.json" |
        Where-Object { $_.Name -eq ([string]([char]0x6253) + [char]0x5305 + [char]0x914D + [char]0x7F6E + ".json") } |
        Select-Object -First 1 -ExpandProperty FullName)
}
if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) {
    throw "Packaging config was not found."
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Test-PrivateIpv4([string]$Value) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$parsed)) { return $false }
    if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $bytes = $parsed.GetAddressBytes()
    return (($bytes[0] -eq 10) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168))
}

function Find-LanIpv4 {
    try {
        $routes = @(Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop |
            Sort-Object RouteMetric, InterfaceMetric)
        foreach ($route in $routes) {
            $addresses = @(Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex `
                -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue)
            foreach ($item in $addresses) {
                if (-not $item.SkipAsSource -and (Test-PrivateIpv4 $item.IPAddress)) {
                    return $item.IPAddress
                }
            }
        }
    } catch { }

    $fallback = @([Net.Dns]::GetHostAddresses([Net.Dns]::GetHostName()) |
        Where-Object { $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork } |
        ForEach-Object { $_.IPAddressToString })
    foreach ($candidate in $fallback) {
        if (Test-PrivateIpv4 $candidate) { return $candidate }
    }
    throw "No private LAN IPv4 address was found. Connect this PC to the target LAN and retry."
}

function Find-DesktopPython {
    $preferred = "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
    if (Test-Path -LiteralPath $preferred) { return $preferred }
    foreach ($name in @("python", "python3")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $resolved = (& $command.Source -c "import sys; print(sys.executable)").Trim()
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $resolved)) {
                return (Resolve-Path -LiteralPath $resolved).Path
            }
        } catch { }
    }
    throw "Python 3.13 was not found. Install the desktop Python environment and retry."
}

function Test-TcpPort([string]$HostName, [int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $attempt = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $attempt.AsyncWaitHandle.WaitOne(1500)) { return $false }
        $client.EndConnect($attempt)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$configuredIp = [string]$config.server_ip
$serverIp = if (-not $configuredIp -or $configuredIp.Trim().ToLowerInvariant() -eq "auto") {
    Find-LanIpv4
} else {
    $configuredIp.Trim()
}
if (-not (Test-PrivateIpv4 $serverIp)) {
    throw "Configured server_ip is not a private LAN IPv4 address: $serverIp"
}

$expiresAt = [DateTimeOffset]::Parse([string]$config.expires_at)
if ($expiresAt -le [DateTimeOffset]::Now) {
    throw "The configured release expiry is already in the past: $expiresAt"
}
$versionPrefix = ([string]$config.version_prefix).Trim()
if (-not $versionPrefix) { $versionPrefix = "1.0.0-local" }
$version = "$versionPrefix-$(Get-Date -Format 'yyyyMMdd-HHmm')"
$distPath = ([string]$config.output_dir).Trim()
if (-not $distPath) { $distPath = "build\release-local" }
$pythonExe = Find-DesktopPython
$serverReady = Test-TcpPort $serverIp 8000

# A running one-file EXE locks itself on Windows.  Do not waste a full build
# only to fail at the final write; automatically select a timestamped sibling
# folder when the configured output is currently in use.
$absoluteConfiguredDist = if ([IO.Path]::IsPathRooted($distPath)) {
    [IO.Path]::GetFullPath($distPath)
} else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $distPath))
}
$configuredExe = Join-Path $absoluteConfiguredDist "CreativeEnginePro.exe"
if (Test-Path -LiteralPath $configuredExe) {
    try {
        $probe = [IO.File]::Open(
            $configuredExe, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None)
        $probe.Dispose()
    } catch {
        $distPath = "$distPath-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Write-Warning "The previous EXE is running. This build will use: $distPath"
    }
}

Write-Host ""
Write-Host "One-click package configuration" -ForegroundColor Cyan
Write-Host "  Server:  http://${serverIp}:8000"
Write-Host "  Version: $version"
Write-Host "  Expires: $($expiresAt.ToString('yyyy-MM-ddTHH:mm:sszzz'))"
Write-Host "  Python:  $pythonExe"
Write-Host "  Output:  $distPath"
if ($serverReady) {
    Write-Host "  Auth API: reachable" -ForegroundColor Green
} else {
    Write-Warning "The auth API is currently offline. Packaging can continue, but users cannot log in until the local server is started."
}

if ($PreflightOnly) {
    Write-Host "PREFLIGHT_OK" -ForegroundColor Green
    exit 0
}

$buildDir = Join-Path $projectRoot "build"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$logPath = Join-Path $buildDir "one-click-package.log"
$transcriptStarted = $false
try {
    try {
        Start-Transcript -LiteralPath $logPath -Force | Out-Null
        $transcriptStarted = $true
    } catch { }

    & (Join-Path $PSScriptRoot "build_local_release.ps1") `
        -ServerIp $serverIp `
        -PythonExe $pythonExe `
        -Version $version `
        -ExpiresAfter $expiresAt `
        -DistPath $distPath
    if ($LASTEXITCODE -ne 0) {
        throw "The release build returned exit code $LASTEXITCODE."
    }

    $absoluteDist = if ([IO.Path]::IsPathRooted($distPath)) {
        [IO.Path]::GetFullPath($distPath)
    } else {
        [IO.Path]::GetFullPath((Join-Path $projectRoot $distPath))
    }
    $exePath = Join-Path $absoluteDist "CreativeEnginePro.exe"
    $hashPath = Join-Path $absoluteDist "CreativeEnginePro.sha256.txt"
    if (-not (Test-Path -LiteralPath $exePath) -or -not (Test-Path -LiteralPath $hashPath)) {
        throw "Build validation completed without the expected EXE and SHA256 files."
    }

    Write-Host ""
    Write-Host "ONE_CLICK_PACKAGE_OK" -ForegroundColor Green
    Write-Host "EXE: $exePath"
    Write-Host "SHA256: $((Get-Content -LiteralPath $hashPath -Raw).Trim())"
    Start-Process -FilePath "explorer.exe" -ArgumentList "/select,`"$exePath`""
} catch {
    Write-Host ""
    Write-Host "ONE_CLICK_PACKAGE_FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}

exit 0
