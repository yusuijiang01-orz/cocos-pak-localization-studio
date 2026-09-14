@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo Cocos2D PAK Localization Studio V3-A
echo ============================================================
echo.

set "APP_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%APP_PY%" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python environment was not found. Run INSTALL_DEPS.bat first.
    pause
    exit /b 1
  )
  set "APP_PY=python"
)
"%APP_PY%" --version
if errorlevel 1 (
  echo [ERROR] Python could not start correctly.
  pause
  exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js was not found.
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found.
  pause
  exit /b 1
)

if not exist "node_modules\electron\package.json" (
  echo [INFO] Electron dependencies are not installed yet.
  echo Run INSTALL_DEPS.bat first.
  pause
  exit /b 1
)

echo Starting application...
call npm start
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] Electron exited with code %RC%.
  pause
  exit /b %RC%
)
exit /b 0
