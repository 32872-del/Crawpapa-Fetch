@echo off
setlocal

echo.
echo ========================================
echo   Crawler MCP Server v4.0 Portable Install
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo [0/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.10 or newer first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%i"
echo Python %PYTHON_VERSION% found.

for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    if %%a LSS 3 (
        echo [ERROR] Python 3.10 or newer is required. Current: %PYTHON_VERSION%
        pause
        exit /b 1
    )
    if %%a EQU 3 (
        if %%b LSS 10 (
            echo [ERROR] Python 3.10 or newer is required. Current: %PYTHON_VERSION%
            pause
            exit /b 1
        )
    )
)

echo.
echo [1/6] Installing uv...
python -m pip install uv -q
if errorlevel 1 (
    echo [WARN] uv install failed. Falling back to pip mode.
    set "USE_PIP=1"
) else (
    echo uv is ready.
    set "USE_PIP=0"
)

echo.
echo [2/6] Creating venv and installing dependencies...
if "%USE_PIP%"=="1" (
    if not exist ".venv" python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -e ".[full,dev]"
) else (
    uv sync --extra full --extra dev
    if errorlevel 1 (
        echo [WARN] uv sync failed. Trying uv pip install...
        uv venv .venv
        uv pip install -e ".[full,dev]"
    )
)
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)
echo Dependencies installed.

echo.
echo [3/6] Installing optional crawler components...
if "%USE_PIP%"=="1" (
    ".venv\Scripts\python.exe" -m pip install curl_cffi fake-useragent "httpx[http2]" anyio parsel jsonpath-ng playwright
) else (
    uv pip install curl_cffi fake-useragent "httpx[http2]" anyio parsel jsonpath-ng playwright
)
if errorlevel 1 (
    echo [WARN] Some optional components failed. Basic request mode still works.
) else (
    echo Optional components installed.
)

echo.
echo [4/6] Installing Chromium for Playwright...
if "%USE_PIP%"=="1" (
    ".venv\Scripts\python.exe" -m playwright install chromium
) else (
    uv run playwright install chromium
)
if errorlevel 1 (
    echo [WARN] Chromium install failed. Browser mode may be unavailable.
) else (
    echo Browser engine installed.
)

echo.
echo [5/6] Creating runtime directories...
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
echo [6/6] Generating MCP client configs...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" setup_mcp_clients.py
) else (
    python setup_mcp_clients.py
)
if errorlevel 1 (
    echo [WARN] Config generation failed. Run setup_mcp_clients.py later.
) else (
    echo Configs generated.
)

echo.
echo ========================================
echo   Portable install complete
echo ========================================
echo.
echo Project path: %PROJECT_DIR%
echo Next: restart Codex, Claude Code, or VS Code.
echo.
pause
