#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "========================================"
echo "  Crawpapa-Fetch v5.2 Unix Installer"
echo "========================================"
echo

PYTHON_BIN="${PYTHON:-python3}"

echo "[1/7] Checking Python..."
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] Python 3.10 or newer was not found."
  echo "macOS: brew install python"
  echo "Ubuntu/Debian: sudo apt-get install python3 python3-venv python3-pip"
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required. Current: {sys.version.split()[0]}")
print(f"Python {sys.version.split()[0]} found.")
PY

echo
echo "[2/7] Installing uv..."
if "$PYTHON_BIN" -m pip install -q uv; then
  USE_UV=1
  echo "uv is ready."
else
  USE_UV=0
  echo "[WARN] uv install failed. Falling back to pip mode."
fi

echo
echo "[3/7] Creating venv and installing dependencies..."
if [ "$USE_UV" = "1" ]; then
  if ! uv sync --extra full --extra dev; then
    echo "[WARN] uv sync failed. Trying uv pip install..."
    uv venv .venv
    uv pip install -e ".[full,dev]"
  fi
else
  [ -d ".venv" ] || "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e ".[full,dev]"
fi
echo "Dependencies installed."

echo
echo "[4/7] Installing optional crawler components..."
if [ "$USE_UV" = "1" ]; then
  uv pip install curl_cffi fake-useragent "httpx[http2]" anyio parsel jsonpath-ng playwright || true
else
  .venv/bin/python -m pip install curl_cffi fake-useragent "httpx[http2]" anyio parsel jsonpath-ng playwright || true
fi
echo "Optional component step finished."

echo
echo "[5/7] Installing Chromium for Playwright..."
if [ "$USE_UV" = "1" ]; then
  uv run playwright install chromium || echo "[WARN] Chromium install failed. Browser mode may be unavailable."
else
  .venv/bin/python -m playwright install chromium || echo "[WARN] Chromium install failed. Browser mode may be unavailable."
fi

echo
echo "[6/7] Creating runtime directories..."
mkdir -p output cache databases schemas logs jobs frontier templates cookies
echo "Runtime directories are ready."

echo
echo "[7/7] Generating MCP client configs..."
.venv/bin/python setup_mcp_clients.py || echo "[WARN] Config generation failed. Run .venv/bin/python setup_mcp_clients.py later."

echo
echo "========================================"
echo "  Install complete"
echo "========================================"
echo
echo "Next steps:"
echo "  1. Restart Codex, Claude Code, or VS Code."
echo "  2. Run ./start.sh to check this installation."
