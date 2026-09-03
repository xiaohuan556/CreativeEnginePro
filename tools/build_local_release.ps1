param(
    [string]$ServerIp = "10.13.12.67",
    [string]$PythonExe = "",
    [string]$Version = "1.0.0-local",
    [DateTimeOffset]$ExpiresAfter = "2026-12-02T00:00:00+08:00",
    [string]$DistPath = "build\release-local",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$arguments = @{
    AuthBaseUrl = "http://${ServerIp}:8000"
    Version = $Version
    ExpiresAfter = $ExpiresAfter
    DistPath = $DistPath
    AllowInsecureLan = $true
    SkipTests = $SkipTests
}
if ($PythonExe) { $arguments.PythonExe = $PythonExe }
& (Join-Path $PSScriptRoot "build_release.ps1") @arguments
