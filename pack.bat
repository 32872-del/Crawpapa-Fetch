@echo off
setlocal

echo.
echo ========================================
echo   Crawpapa-Fetch Package Builder
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

%PYTHON% tools\maintenance\secret_audit.py
if errorlevel 1 (
    echo [ERROR] Secret audit failed. Fix findings before packaging.
    if /i not "%PACK_NO_PAUSE%"=="1" pause
    exit /b 1
)

%PYTHON% tools\maintenance\build_package.py
if errorlevel 1 (
    echo [ERROR] Build failed.
    if /i not "%PACK_NO_PAUSE%"=="1" pause
    exit /b 1
)

echo.
echo Package artifacts are in dist\
echo.
if /i not "%PACK_NO_PAUSE%"=="1" pause
