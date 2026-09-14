@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo Cocos2D PAK Localization Studio - Complete Installer
echo ============================================================
echo.

where node >nul 2>&1 || goto :no_node
where npm >nul 2>&1 || goto :no_node
echo Node:
node --version
echo npm:
call npm --version || goto :fail

echo.
echo [A/2] Installing Electron dependencies...
call npm install || goto :fail

echo.
echo [B/2] Installing isolated Python, CUDA and local model dependencies...
call "%~dp0INSTALL_LOCAL_MODEL.bat" --no-pause || goto :fail

echo.
echo [OK] All dependencies are installed.
echo Run START_V3A.bat to launch the application.
pause
exit /b 0

:no_node
echo [ERROR] Node.js LTS and npm are required. Install Node.js, then retry.
goto :fail

:fail
echo.
echo [FAILED] Dependency installation did not complete.
pause
exit /b 1
