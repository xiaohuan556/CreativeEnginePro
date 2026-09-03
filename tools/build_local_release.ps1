param(
    [string]$ServerIp = "10.13.12.67",
    [string]$PythonExe = "",
    [string]$Version = "1.0.0-local",
    [string]$DistPath = "build\release-local",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$arguments = @{
    AuthBaseUrl = "http://${ServerIp}:8000"
    Version = $Version
    DistPath = $DistPath
    AllowInsecureLan = $true
    SkipTests = $SkipTests
}
if ($PythonExe) { $arguments.PythonExe = $PythonExe }
& (Join-Path $PSScriptRoot "build_release.ps1") @arguments
