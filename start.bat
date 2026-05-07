@echo off
setlocal

echo.
echo ========================================
echo   Crawler MCP Server v4.0 Check
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo [1/5] Checking Python environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv was not found. Please run install_portable.bat first.
    pause
    exit /b 1
)
echo Python venv is ready.

echo.
echo [2/5] Checking core dependencies...
".venv\Scripts\python.exe" -c "import mcp, requests, bs4, pydantic"
if errorlevel 1 (
    echo [ERROR] Core dependencies are missing. Please rerun install_portable.bat.
    pause
    exit /b 1
)
echo Core dependencies are installed.

echo.
echo [3/5] Checking optional components...
".venv\Scripts\python.exe" -c "import curl_cffi; from fake_useragent import UserAgent"
if errorlevel 1 (
    echo [WARN] curl_cffi or fake_useragent is missing.
) else (
    echo Anti-detect components are installed.
)

".venv\Scripts\python.exe" -c "from playwright.sync_api import sync_playwright"
if errorlevel 1 (
    echo [WARN] Playwright is missing.
) else (
    echo Playwright package is installed.
)

echo.
echo [4/5] Checking MCP client configs...
if exist ".mcp.json" (
    echo Found .mcp.json
) else (
    echo [WARN] .mcp.json was not found.
)
if exist ".codex\config.toml" (
    echo Found .codex\config.toml
) else (
    echo [WARN] .codex\config.toml was not found.
)
if exist ".vscode\mcp.json" (
    echo Found .vscode\mcp.json
) else (
    echo [WARN] .vscode\mcp.json was not found.
)

echo.
echo [5/5] Running built-in diagnosis...
".venv\Scripts\python.exe" -c "import json, unified_crawler_server as s; print(json.dumps(json.loads(s.diagnose_crawler_setup())['summary'], ensure_ascii=False, indent=2))"
if errorlevel 1 (
    echo [ERROR] Diagnosis failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Check complete
echo ========================================
echo.
echo To start as an MCP server, use:
echo   ".venv\Scripts\python.exe" unified_crawler_server.py
echo.
pause
