@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=F:\Miniconda39\envs\python31013\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  1/3 构建 Python 后端（PyInstaller onedir）
echo ============================================================
"%PY%" -m PyInstaller desktop\backend.spec --noconfirm --distpath build\backend --workpath build\pyinstaller
if errorlevel 1 goto fail
if not exist "build\backend\DPCBackend\DPCBackend.exe" goto fail

echo.
echo ============================================================
echo  2/3 安装 Electron 依赖
echo ============================================================
if not exist "node_modules" (
  call npm install
  if errorlevel 1 goto fail
)

echo.
echo ============================================================
echo  3/3 打包文件夹版客户端
echo ============================================================
call npm run dist:dir
if errorlevel 1 goto fail

echo.
echo 构建完成：
echo   %cd%\dist_client\win-unpacked\DrissionPageCatcher.exe
echo.
if /i not "%DPC_NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo 构建失败，请查看上面的错误信息。
if /i not "%DPC_NO_PAUSE%"=="1" pause
exit /b 1
