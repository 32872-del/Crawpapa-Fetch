#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo
echo "========================================"
echo "  Crawpapa-Fetch v5.2 Check"
echo "========================================"
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "[ERROR] .venv was not found. Please run ./install.sh first."
  exit 1
fi

echo "[1/5] Checking core dependencies..."
.venv/bin/python -c "import mcp, requests, bs4, pydantic"
echo "Core dependencies are installed."

echo
echo "[2/5] Checking optional components..."
if .venv/bin/python -c "import curl_cffi; from fake_useragent import UserAgent" >/dev/null 2>&1; then
  echo "Anti-detect components are installed."
else
  echo "[WARN] curl_cffi or fake-useragent is missing."
fi

if .venv/bin/python -c "from playwright.sync_api import sync_playwright" >/dev/null 2>&1; then
  echo "Playwright package is installed."
else
  echo "[WARN] Playwright is missing."
fi

echo
echo "[3/5] Checking MCP client configs..."
[ -f ".mcp.json" ] && echo "Found .mcp.json" || echo "[WARN] .mcp.json was not found."
[ -f ".codex/config.toml" ] && echo "Found .codex/config.toml" || echo "[WARN] .codex/config.toml was not found."
[ -f ".vscode/mcp.json" ] && echo "Found .vscode/mcp.json" || echo "[WARN] .vscode/mcp.json was not found."

echo
echo "[4/5] Running built-in diagnosis..."
.venv/bin/python - <<'PY'
import json
import unified_crawler_server as s
print(json.dumps(json.loads(s.diagnose_crawler_setup())["summary"], ensure_ascii=False, indent=2))
PY

echo
echo "[5/5] Server command"
echo "To start as an MCP server, use:"
echo "  .venv/bin/python unified_crawler_server.py"

echo
echo "========================================"
echo "  Check complete"
echo "========================================"
