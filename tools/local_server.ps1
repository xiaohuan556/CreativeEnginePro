param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Start",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeDir = Join-Path $projectRoot "local_server_data"
$logDir = Join-Path $runtimeDir "logs"
$mediaDir = Join-Path $runtimeDir "media"
$pidFile = Join-Path $runtimeDir "processes.json"

function Find-Python {
    if ($PythonExe) { return (Resolve-Path -LiteralPath $PythonExe).Path }
    # Development uses the desktop Python installation.  A server-only venv
    # must never become the interpreter used to launch the PyQt application.
    $desktopPython = "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
    if (Test-Path -LiteralPath $desktopPython) { return $desktopPython }
    foreach ($name in @("python", "python3")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate) { return $candidate.Source }
    }
    $desktopVenvPython = Join-Path $projectRoot "server\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $desktopVenvPython) { return $desktopVenvPython }
    throw "Python was not found. Install 64-bit Python or pass -PythonExe."
}

function Read-ProcessState {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    try { return Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json }
    catch { return $null }
}

function Test-TrackedProcess([object]$item) {
    if (-not $item -or -not $item.pid) { return $false }
    return $null -ne (Get-Process -Id ([int]$item.pid) -ErrorAction SilentlyContinue)
}

if ($Action -eq "Stop") {
    $state = Read-ProcessState
    if ($state) {
        # The web entry is kept only to clean up process files created by an
        # earlier desktop launcher. New launches never start the web product.
        foreach ($item in @($state.web, $state.worker, $state.api)) {
            if (Test-TrackedProcess $item) {
                # A Windows venv launcher may own a child Python process. Stop
                # the complete tree so no API/worker is orphaned after shutdown.
                try { & taskkill.exe /PID ([int]$item.pid) /T /F 2>$null | Out-Null }
                catch { }
                if (Test-TrackedProcess $item) {
                    Stop-Process -Id ([int]$item.pid) -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Local server stopped." -ForegroundColor Green
    exit 0
}

if ($Action -eq "Status") {
    $state = Read-ProcessState
    if (-not $state) { Write-Host "Local server is not running."; exit 1 }
    foreach ($name in @("api", "worker")) {
        $item = $state.$name
        if ($item) { Write-Host "$name : $(if (Test-TrackedProcess $item) {'running'} else {'stopped'}) (PID $($item.pid))" }
    }
    exit 0
}

$oldState = Read-ProcessState
if ($oldState -and (Test-TrackedProcess $oldState.api)) {
    Write-Host "Local server is already running." -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir, $mediaDir | Out-Null
$python = Find-Python
$venvRoot = Join-Path $projectRoot "server\.venv"
if ($python -eq (Join-Path $venvRoot "Scripts\python.exe")) {
    # Launch the base executable directly and provide this project's own venv
    # packages explicitly. This keeps the tracked PID equal to the real server
    # process instead of a short-lived Windows venv launcher.
    $basePython = (& $python -c "import sys; print(sys._base_executable)").Trim()
    if ($basePython -and (Test-Path -LiteralPath $basePython)) {
        $python = $basePython
        $env:PYTHONPATH = "$venvRoot\Lib\site-packages;$projectRoot;$projectRoot\server"
    }
}
$databasePath = (Join-Path $runtimeDir "creative_engine_server.db").Replace("\", "/")
$env:CEP_DATABASE_URL = "sqlite:///$databasePath"
$env:CEP_PUBLIC_ORIGIN = "http://127.0.0.1:8000"
$env:CEP_STORAGE_DIR = $mediaDir
if (-not $env:PYTHONPATH) { $env:PYTHONPATH = "$projectRoot;$projectRoot\server" }

$api = Start-Process -FilePath $python -ArgumentList @(
    "-m", "uvicorn", "creative_server.main:app", "--host", "0.0.0.0", "--port", "8000"
) -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "api.out.log") `
    -RedirectStandardError (Join-Path $logDir "api.err.log")

$worker = Start-Process -FilePath $python -ArgumentList @(
    "-m", "creative_server.worker"
) -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "worker.out.log") `
    -RedirectStandardError (Join-Path $logDir "worker.err.log")

$deadline = (Get-Date).AddSeconds(45)
$ready = $false
do {
    Start-Sleep -Milliseconds 500
    try {
        $socket = New-Object Net.Sockets.TcpClient
        $attempt = $socket.BeginConnect("127.0.0.1", 8000, $null, $null)
        if ($attempt.AsyncWaitHandle.WaitOne(1500)) {
            $socket.EndConnect($attempt)
            $ready = $socket.Connected
        }
        $socket.Dispose()
    } catch { $ready = $false }
} until ($ready -or (Get-Date) -ge $deadline -or $api.HasExited)

if (-not $ready -or $api.HasExited) {
    Stop-Process -Id $api.Id, $worker.Id -Force -ErrorAction SilentlyContinue
    throw "Local API failed to start. Read $logDir\api.err.log"
}

$state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    api = @{ pid = $api.Id }
    worker = @{ pid = $worker.Id }
}
[IO.File]::WriteAllText($pidFile, ($state | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))

Write-Host "Local account and task server started." -ForegroundColor Green
Write-Host "Local API: http://127.0.0.1:8000"
Write-Host "LAN API: http://10.13.12.67:8000"
Write-Host "Data: $runtimeDir"
