#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=".venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON:-python3}"
fi

echo "========================================"
echo "  Crawpapa-Fetch Package Build"
echo "========================================"

"$PYTHON_BIN" tools/maintenance/secret_audit.py
"$PYTHON_BIN" tools/maintenance/build_package.py "$@"
