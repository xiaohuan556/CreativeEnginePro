@echo off
echo ========================================
echo     CreativeEnginePro 正在打包...
echo ========================================
echo.

:: 自动切换到 bat 文件所在的当前项目目录
cd /d "%~dp0"

echo 当前目录 %CD%
echo 开始打包（已启用 --clean）...
echo.

python -m PyInstaller --clean main.spec

echo.
echo ========================================
echo 打包完成！单文件 EXE 已生成在 dist 文件夹里
echo ========================================
pause