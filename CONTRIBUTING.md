# Contributing

Thanks for improving Crawpapa-Fetch.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,dev]"
.\.venv\Scripts\playwright.exe install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

## Project Rules

- Keep runtime outputs out of git. `cache/`, `cookies/`, `databases/`, `frontier/`, `jobs/`, `logs/`, and `output/` should only track `.gitkeep`.
- Put reusable task scripts in `tools/`, not `output/`.
- Put durable crawler logic in `crawler_core/`.
- Keep MCP tool names stable unless there is a clear migration path.
- Do not add code that bypasses CAPTCHA, login walls, access controls, or private network protections.

## Pull Request Checklist

- Tests pass: `python -m pytest -q`
- Secret audit passes: `python tools/maintenance/secret_audit.py`
- Docs are updated for user-facing changes.
- New MCP tools return structured `ok/version/data/diagnostics/recommendations` when practical.

