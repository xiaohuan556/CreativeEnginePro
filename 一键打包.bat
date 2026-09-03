@echo off
chcp 65001 >nul
title CreativeEnginePro 一键打包
cd /d "%~dp0"

echo ============================================================
echo CreativeEnginePro 正式版一键打包
echo ============================================================
echo 将自动执行：环境检查、全量测试、EXE 打包、成品验证和校验码生成。
echo 打包过程中请不要关闭此窗口。
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\one_click_build.ps1"
set "CEP_BUILD_EXIT=%ERRORLEVEL%"

echo.
if not "%CEP_BUILD_EXIT%"=="0" goto build_failed
echo [成功] 正式版已经生成，成品目录已打开。
echo 可以发送 CreativeEnginePro.exe；校验文件建议一并保留。
pause
exit /b 0

:build_failed
echo [失败] 打包没有完成，请把本窗口中的错误信息发给开发者。
echo 详细日志：%~dp0build\one-click-package.log
pause
exit /b %CEP_BUILD_EXIT%
