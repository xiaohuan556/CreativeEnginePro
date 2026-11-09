@echo off
chcp 65001 >nul
set "CEP_PROJECT_ROOT=%~dp0..\.."
cd /d "%CEP_PROJECT_ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CEP_PROJECT_ROOT%\tools\create_local_admin.ps1"
echo.
pause
