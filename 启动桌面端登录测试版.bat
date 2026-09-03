@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CEP_DESKTOP_PYTHON=C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\python3.13.exe"
set "CEP_RELEASE_MANIFEST=%~dp0build\release\release_manifest.json"
if not exist "%CEP_DESKTOP_PYTHON%" goto python_missing
if not exist "%CEP_RELEASE_MANIFEST%" goto manifest_missing
"%CEP_DESKTOP_PYTHON%" "%~dp0main.py"
if errorlevel 1 pause
exit /b %errorlevel%

:manifest_missing
echo 未找到上一次正式构建生成的授权清单：
echo %CEP_RELEASE_MANIFEST%
echo 请先保留 build\release\release_manifest.json，或使用普通开发版启动脚本。
pause
exit /b 1

:python_missing
echo 未找到桌面开发环境 Python 3.13，请重新安装 Python 或联系开发者。
pause
exit /b 1
