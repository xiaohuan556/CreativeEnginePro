@echo off
chcp 65001 >nul
where node >nul 2>nul
if errorlevel 1 (
  echo 未找到 Duo 使用的 Node.js，请使用服务器原来的 Node 环境。
  pause
  exit /b 1
)
node "%~dp0install-delivery.mjs" %*
set "DUO_DELIVERY_EXIT=%ERRORLEVEL%"
pause
exit /b %DUO_DELIVERY_EXIT%
