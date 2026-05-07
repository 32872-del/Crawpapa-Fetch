@echo off
setlocal

echo ========================================
echo   Crawpapa-Fetch v5.2 Installer
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.10 or newer first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%i"
echo Python %PYTHON_VERSION% found.

echo.
echo [2/7] Installing uv...
python -m pip install uv -q
if errorlevel 1 (
    echo [ERROR] Failed to install uv.
    pause
    exit /b 1
)
echo uv is ready.

echo.
echo [3/7] Installing project dependencies...
uv sync --extra full --extra dev
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)
echo Dependencies installed.

echo.
echo [4/7] Installing optional crawler components...
uv pip install curl_cffi fake-useragent "httpx[http2]" anyio parsel jsonpath-ng playwright
if errorlevel 1 (
    echo [WARN] Some optional dependencies failed. Basic tools may still work.
) else (
    echo Optional components installed.
)

echo.
echo [5/7] Installing Chromium for Playwright...
uv run playwright install chromium
if errorlevel 1 (
    echo [WARN] Chromium install failed. Browser mode may be unavailable.
    echo You can retry later with:
    echo   uv run playwright install chromium
) else (
    echo Chromium installed.
)

echo.
echo [6/7] Creating runtime directories...
if not exist "output" mkdir output
if not exist "cache" mkdir cache
if not exist "databases" mkdir databases
if not exist "schemas" mkdir schemas
if not exist "logs" mkdir logs
if not exist "jobs" mkdir jobs
if not exist "frontier" mkdir frontier
if not exist "templates" mkdir templates
if not exist "cookies" mkdir cookies
echo Runtime directories are ready.

echo.
echo [7/7] Generating MCP client configs...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" setup_mcp_clients.py
) else (
    uv run python setup_mcp_clients.py
)
if errorlevel 1 (
    echo [WARN] Config generation failed. Run this later:
    echo   uv run python setup_mcp_clients.py
) else (
    echo MCP client configs generated.
)

echo.
echo ========================================
echo   Install complete
echo ========================================
echo.
echo Next steps:
echo   1. Restart Codex, Claude Code, or VS Code.
echo   2. Run start.bat to check this installation.
echo.
pause
