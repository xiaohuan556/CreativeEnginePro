@echo off
echo ========================================
echo     CreativeEnginePro 正在打包...
echo ========================================
echo.

:: 自动切换到 bat 文件所在的当前项目目录
cd /d "%~dp0"

echo 当前目录 %CD%
echo.
set /p CEP_AUTH_URL=请输入正式登录服务器 HTTPS 地址:
if "%CEP_AUTH_URL%"=="" (
  echo 错误：正式版必须配置登录服务器地址。
  pause
  exit /b 1
)

echo 开始测试并打包正式版（已启用 --clean）...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_release.ps1" -AuthBaseUrl "%CEP_AUTH_URL%"
if errorlevel 1 (
  echo.
  echo 打包失败，请查看上方错误信息。
  pause
  exit /b 1
)

echo.
echo ========================================
echo 打包完成！单文件 EXE 已生成在 dist 文件夹里
echo ========================================
pause
