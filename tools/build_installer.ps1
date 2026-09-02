param(
    [string]$IsccExe = "",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopExe = Join-Path $projectRoot "dist\CreativeEnginePro.exe"
if (-not (Test-Path -LiteralPath $desktopExe)) {
    throw "Run tools\build_release.ps1 before building the installer."
}

if (-not $IsccExe) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $IsccExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $IsccExe) {
    throw "Inno Setup 6 was not found. Pass its full path with -IsccExe."
}

$script = Join-Path $projectRoot "installer\CreativeEnginePro.iss"
& $IsccExe "/DMyAppVersion=$Version" $script
if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }

Write-Host "Installer created in the dist directory." -ForegroundColor Green
