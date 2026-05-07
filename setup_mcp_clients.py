#!/usr/bin/env python3
"""Generate local MCP client configuration for Codex, Claude Code, and VS Code."""
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _as_posix(path: Path) -> str:
    return path.resolve().as_posix()


def _python_command() -> str:
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _as_posix(candidate)
    return sys.executable or "python"


def _workspace_python_expr() -> str:
    if os.name == "nt":
        return "${workspaceFolder}/.venv/Scripts/python.exe"
    return "${workspaceFolder}/.venv/bin/python"


def _shared_env() -> dict[str, str]:
    data_root = ROOT / ".crawler-data"
    return {
        "PYTHONPATH": _as_posix(ROOT),
        "CRAWLER_DATA_DIR": _as_posix(data_root),
        "CRAWLER_VERIFY_TLS": "true",
        "CRAWLER_RESPECT_ROBOTS": "true",
        "CRAWLER_ALLOW_PRIVATE_NETS": "false",
        "CRAWLER_PERSIST_COOKIES": "true",
        "CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE": "false",
        "CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE": "false",
    }


def write_codex_config(command: str) -> Path:
    target = ROOT / ".codex" / "config.toml"
    target.parent.mkdir(exist_ok=True)
    env = _shared_env()
    server_script = _as_posix(ROOT / "unified_crawler_server.py")
    content = f"""[mcp_servers.crawler]
command = "{command}"
args = ["{server_script}"]
cwd = "{_as_posix(ROOT)}"
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true

[mcp_servers.crawler.env]
PYTHONPATH = "{env['PYTHONPATH']}"
CRAWLER_DATA_DIR = "{env['CRAWLER_DATA_DIR']}"
CRAWLER_VERIFY_TLS = "true"
CRAWLER_RESPECT_ROBOTS = "true"
CRAWLER_ALLOW_PRIVATE_NETS = "false"
CRAWLER_PERSIST_COOKIES = "true"
CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE = "false"
CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE = "false"
"""
    target.write_text(content, encoding="utf-8")
    return target


def write_claude_project_config(command: str) -> Path:
    target = ROOT / ".mcp.json"
    config = {
        "mcpServers": {
            "crawler": {
                "command": command,
                "args": [_as_posix(ROOT / "unified_crawler_server.py")],
                "cwd": _as_posix(ROOT),
                "env": _shared_env(),
            }
        }
    }
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_vscode_config() -> Path:
    target = ROOT / ".vscode" / "mcp.json"
    target.parent.mkdir(exist_ok=True)
    config = {
        "inputs": [],
        "servers": {
            "crawler": {
                "type": "stdio",
                "command": _workspace_python_expr(),
                "args": ["${workspaceFolder}/unified_crawler_server.py"],
                "env": {
                    "PYTHONPATH": "${workspaceFolder}",
                    "CRAWLER_DATA_DIR": "${workspaceFolder}/.crawler-data",
                    "CRAWLER_VERIFY_TLS": "true",
                    "CRAWLER_RESPECT_ROBOTS": "true",
                    "CRAWLER_ALLOW_PRIVATE_NETS": "false",
                    "CRAWLER_PERSIST_COOKIES": "true",
                    "CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE": "false",
                    "CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE": "false",
                },
                "dev": {
                    "watch": "${workspaceFolder}/**/*.py",
                    "debug": {"type": "python"},
                },
            }
        },
    }
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> int:
    command = _python_command()
    files = [
        write_codex_config(command),
        write_claude_project_config(command),
        write_vscode_config(),
    ]
    print("Generated MCP client configs:")
    for file in files:
        print(f"- {file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
