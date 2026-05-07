@echo off
setlocal enabledelayedexpansion

echo.
echo ========================================
echo   Packaging Crawler MCP Server
echo   Build distributable zip
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

set "PACK_NAME=crawler-mcp-server-v4.0"
set "PACK_DIR=%TEMP%\%PACK_NAME%"
set "OUTPUT_ZIP=%PROJECT_DIR%%PACK_NAME%.zip"

if exist "%PACK_DIR%" rmdir /s /q "%PACK_DIR%"
mkdir "%PACK_DIR%"

echo [1/6] Copy core files...
copy /y "unified_crawler_server.py" "%PACK_DIR%\" >nul
copy /y "main.py" "%PACK_DIR%\" >nul
copy /y "setup_mcp_clients.py" "%PACK_DIR%\" >nul
copy /y "pyproject.toml" "%PACK_DIR%\" >nul
copy /y "uv.lock" "%PACK_DIR%\" >nul
copy /y "proxy_pool.json" "%PACK_DIR%\" >nul
copy /y ".env.example" "%PACK_DIR%\" >nul
if not exist "%PACK_DIR%\crawler_core" mkdir "%PACK_DIR%\crawler_core"
copy /y "crawler_core\*.py" "%PACK_DIR%\crawler_core\" >nul
if exist "agents" xcopy /e /i /q /y "agents" "%PACK_DIR%\agents" >nul
if exist "config" xcopy /e /i /q /y "config" "%PACK_DIR%\config" >nul
if exist "utils" xcopy /e /i /q /y "utils" "%PACK_DIR%\utils" >nul
if exist "schemas" xcopy /e /i /q /y "schemas" "%PACK_DIR%\schemas" >nul
if exist "templates" xcopy /e /i /q /y "templates" "%PACK_DIR%\templates" >nul
echo      Core files copied.

echo.
echo [2/6] Copy install scripts...
copy /y "install_portable.bat" "%PACK_DIR%\" >nul
copy /y "install.bat" "%PACK_DIR%\" >nul
copy /y "start.bat" "%PACK_DIR%\" >nul
echo      Install scripts copied.

echo.
echo [3/6] Copy docs...
copy /y "README.md" "%PACK_DIR%\" >nul
if not exist "%PACK_DIR%\docs" mkdir "%PACK_DIR%\docs"
copy /y "docs\SETUP.md" "%PACK_DIR%\docs\" >nul
copy /y "docs\QUICKSTART.md" "%PACK_DIR%\docs\" >nul
copy /y "docs\INTEGRATIONS.md" "%PACK_DIR%\docs\" >nul
echo      Docs copied.

echo.
echo [4/6] Copy tests...
if not exist "%PACK_DIR%\tests" mkdir "%PACK_DIR%\tests"
copy /y "tests\*.py" "%PACK_DIR%\tests\" >nul
echo      Tests copied.

echo.
echo [5/6] Create runtime directories...
mkdir "%PACK_DIR%\output" 2>nul
mkdir "%PACK_DIR%\cache" 2>nul
mkdir "%PACK_DIR%\databases" 2>nul
mkdir "%PACK_DIR%\logs" 2>nul
mkdir "%PACK_DIR%\jobs" 2>nul
mkdir "%PACK_DIR%\frontier" 2>nul
mkdir "%PACK_DIR%\cookies" 2>nul
type nul > "%PACK_DIR%\output\.gitkeep"
type nul > "%PACK_DIR%\cache\.gitkeep"
type nul > "%PACK_DIR%\databases\.gitkeep"
type nul > "%PACK_DIR%\logs\.gitkeep"
type nul > "%PACK_DIR%\jobs\.gitkeep"
type nul > "%PACK_DIR%\frontier\.gitkeep"
type nul > "%PACK_DIR%\cookies\.gitkeep"
echo      Runtime directories created.

echo.
echo [6/6] Create zip...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%PACK_DIR%\*' -DestinationPath '%OUTPUT_ZIP%' -Force"
if errorlevel 1 (
    echo [ERROR] Failed to create zip.
    if /i not "%PACK_NO_PAUSE%"=="1" pause
    exit /b 1
)

rmdir /s /q "%PACK_DIR%"

for %%A in ("%OUTPUT_ZIP%") do set "ZIP_SIZE=%%~zA"
set /a "ZIP_SIZE_MB=%ZIP_SIZE% / 1048576"

echo.
echo ========================================
echo   Package complete
echo ========================================
echo.
echo Output: %OUTPUT_ZIP%
echo Size: %ZIP_SIZE_MB% MB
echo.
echo Usage:
echo   1. Copy the zip to the target machine.
echo   2. Extract it to any directory.
echo   3. Run install_portable.bat.
echo   4. Restart Codex / Claude Code / VS Code.
echo.
if /i not "%PACK_NO_PAUSE%"=="1" pause
