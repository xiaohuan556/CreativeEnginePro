@echo off
chcp 65001 >nul
net session >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="CreativeEnginePro 本地服务器" >nul 2>&1
netsh advfirewall firewall add rule name="CreativeEnginePro 本地服务器" dir=in action=allow protocol=TCP localport=8000 profile=private
echo.
echo 已允许同一局域网设备访问桌面端服务 TCP 8000 端口。
pause
