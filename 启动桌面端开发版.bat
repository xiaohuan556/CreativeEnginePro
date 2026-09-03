@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CEP_DESKTOP_PYTHON=C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
if not exist "%CEP_DESKTOP_PYTHON%" goto python_missing
"%CEP_DESKTOP_PYTHON%" "%~dp0main.py"
if errorlevel 1 pause
exit /b %errorlevel%

:python_missing
echo 未找到桌面开发环境 Python 3.13，请重新安装 Python 或联系开发者。
pause
exit /b 1
