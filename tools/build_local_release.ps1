param(
    [string]$ServerIp = "auto",
    [ValidateRange(1, 65535)]
    [int]$ServerPort = 8765,
    [string]$PythonExe = "",
    [ValidateSet("release", "demo")]
    [string]$Channel = "release",
    [string]$Version = "1.0.0-local",
    [DateTimeOffset]$ExpiresAfter = "2026-12-02T00:00:00+08:00",
    [string]$DistPath = "build\release-local",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
if ($ServerIp -eq "auto") {
    $defaultRoute = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" |
        Sort-Object RouteMetric, InterfaceMetric |
        Select-Object -First 1
    if (-not $defaultRoute) { throw "Could not find the active LAN IPv4 address. Pass -ServerIp explicitly." }
    $address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1
    if (-not $address) { throw "Could not find the active LAN IPv4 address. Pass -ServerIp explicitly." }
    $ServerIp = $address.IPAddress
}
$arguments = @{
    AuthBaseUrl = "http://${ServerIp}:$ServerPort"
    Channel = $Channel
    Version = $Version
    ExpiresAfter = $ExpiresAfter
    DistPath = $DistPath
    AllowInsecureLan = $true
    SkipTests = $SkipTests
}
if ($PythonExe) { $arguments.PythonExe = $PythonExe }
& (Join-Path $PSScriptRoot "build_release.ps1") @arguments
