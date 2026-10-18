@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  DrissionPage 元素捕获器 启动脚本
rem  用法：start.bat [--headless] [--port 8777] [--no-browser]
rem ============================================================

set "PY=F:\Miniconda39\envs\python31013\python.exe"
if not exist "%PY%" (
  echo [提示] 未找到 %PY%，改用 PATH 中的 python
  set "PY=python"
)

cd /d "%~dp0"

"%PY%" -c "import DrissionPage, fastapi, uvicorn" 2>nul
if errorlevel 1 (
  echo [错误] 缺少依赖，请先执行：
  echo     "%PY%" -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo 正在启动 DrissionPage 元素捕获器...
"%PY%" run.py %*

if errorlevel 1 (
  echo.
  echo [错误] 启动失败，请查看上面的日志。
  pause
)
endlocal
