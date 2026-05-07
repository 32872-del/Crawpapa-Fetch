# Linux and macOS Installation

This guide covers local installation on Linux and macOS. Windows users can use `install.bat` or `install_portable.bat`.

## Requirements

- Python `3.10` or newer.
- Git.
- Network access for Python packages and the Playwright Chromium browser.

Recommended system packages:

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git

# macOS with Homebrew
brew install python git
```

## Quick Install

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
chmod +x install.sh start.sh pack.sh
./install.sh
./start.sh
```

The installer creates `.venv`, installs the `full` and `dev` dependency groups, installs Chromium for Playwright, creates runtime directories, and generates MCP client configs.

## Manual Install

Use this path if you prefer explicit commands:

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[full,dev]"
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_mcp_clients.py
.venv/bin/python -m pytest -q
```

Start the MCP server:

```bash
.venv/bin/python unified_crawler_server.py
```

Or use the console entry after installation:

```bash
crawpapa-fetch --server crawler
```

## MCP Client Configs

`setup_mcp_clients.py` generates local configs for:

- Codex: `.codex/config.toml`
- Claude Code: `.mcp.json`
- VS Code MCP: `.vscode/mcp.json`

Generated configs point to:

```bash
${workspaceFolder}/.venv/bin/python
```

Runtime data is isolated under `.crawler-data` by default.

## Browser Dependencies on Linux

If browser mode fails on Linux, install Playwright system dependencies:

```bash
.venv/bin/python -m playwright install --with-deps chromium
```

If you do not have sudo access, try:

```bash
.venv/bin/python -m playwright install chromium
```

Requests and `curl_cffi` modes can still work without browser mode.

## Packaging

```bash
./pack.sh
```

This runs the secret audit and then builds artifacts into `dist/`.

Portable zip only:

```bash
./pack.sh --skip-python-dist
```

Wheel and source distribution require the `build` package:

```bash
.venv/bin/python -m pip install build
./pack.sh
```

## Troubleshooting

If `python3` points to an older version, provide an explicit interpreter:

```bash
PYTHON=python3.12 ./install.sh
```

If `uv` installation fails, the installer falls back to standard `venv` and `pip`.

If MCP tools cannot access public targets, check `CRAWLER_RESPECT_ROBOTS`, proxy settings, and whether the site requires authorization. Crawpapa-Fetch reports CAPTCHA or access-control challenges; it does not bypass them.
