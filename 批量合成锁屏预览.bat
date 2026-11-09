@echo off
%SystemRoot%\System32\chcp.com 65001 >nul
cd /d "%~dp0"
setlocal
set "PYTHONPATH=%CD%"
set "CEP_PYTHON=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
for /d %%D in ("%USERPROFILE%\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_*") do set "PYTHONPATH=%CD%;%%~fD\LocalCache\local-packages\Python313\site-packages"
if not exist "%CEP_PYTHON%" set "CEP_PYTHON=python"
"%CEP_PYTHON%" -X utf8 -m tools.batch_phone_collage "%~dp0..\pic.json" "G:\gh\资源\wallpics\themes\themes\hot" "G:\gh\2026年产出\1106\cp" --lockscreen --jpg-prefix Themepack_ios_GG_MID004_DA003_20261106_ --wallpics-sample "%~dp0outputs\wallpics-argentina\wallpics-reference.png" %*
set "CEP_RESULT=%ERRORLEVEL%"
if /i "%CEP_BATCH_NO_PAUSE%" neq "1" pause
exit /b %CEP_RESULT%
