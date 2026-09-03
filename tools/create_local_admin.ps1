param([string]$PythonExe = "")

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeDir = Join-Path $projectRoot "local_server_data"
$mediaDir = Join-Path $runtimeDir "media"
New-Item -ItemType Directory -Force -Path $runtimeDir, $mediaDir | Out-Null

if (-not $PythonExe) {
    $desktopPython = "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
    if (Test-Path -LiteralPath $desktopPython) { $PythonExe = $desktopPython }
    else {
        $candidate = Get-Command python -ErrorAction SilentlyContinue
        if (-not $candidate) { $candidate = Get-Command python3 -ErrorAction SilentlyContinue }
        if ($candidate) { $PythonExe = $candidate.Source }
        else {
            $desktopVenvPython = Join-Path $projectRoot "server\.venv\Scripts\python.exe"
            if (Test-Path -LiteralPath $desktopVenvPython) { $PythonExe = $desktopVenvPython }
            else { throw "Desktop Python was not found. Install 64-bit Python first." }
        }
    }
}

$databasePath = (Join-Path $runtimeDir "creative_engine_server.db").Replace("\", "/")
$env:CEP_DATABASE_URL = "sqlite:///$databasePath"
$env:CEP_PUBLIC_ORIGIN = "http://127.0.0.1:8000"
$env:CEP_STORAGE_DIR = $mediaDir
$env:PYTHONPATH = "$projectRoot;$projectRoot\server"
Set-Location -LiteralPath $projectRoot
& $PythonExe "server\scripts\create_admin.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
