@echo off
chcp 65001 >nul
set "CEP_PROJECT_ROOT=%~dp0..\.."
net session >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$config = Get-Content -LiteralPath '%CEP_PROJECT_ROOT%\tools\desktop_support\打包配置.json' -Raw -Encoding UTF8 | ConvertFrom-Json; $port = [int]$config.server_port; netsh advfirewall firewall delete rule name='CreativeEnginePro 本地服务器' | Out-Null; netsh advfirewall firewall add rule name='CreativeEnginePro 本地服务器' dir=in action=allow protocol=TCP localport=$port remoteip=localsubnet profile=public; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; Write-Host ('已在公用网络配置下允许本地子网访问 TCP ' + $port + '。')"
if errorlevel 1 (
  echo 防火墙规则设置失败，请以管理员身份重试。
  pause
  exit /b 1
)
echo.
echo 规则仅允许本地子网访问，请勿将该端口映射到互联网。
pause
