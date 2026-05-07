# Crawpapa-Fetch

Crawpapa-Fetch is an agent-oriented crawler analysis MCP server. It helps LLMs and operators understand a website before writing or running a production crawler.

It is designed for:

- Access diagnostics across `requests`, `curl_cffi`, browser rendering, proxies, and authorized cookies.
- Page structure discovery: selectors, initial-state JSON, navigation trees, script URLs, API hints, and structured data.
- Pagination and detail-page scouting.
- Crawl plan drafting, validation, and export.
- Structured data quality checks such as job record normalization and quality grading.

Crawpapa-Fetch is not a CAPTCHA cracker, account bypass tool, or stealth abuse framework. It is intended for lawful analysis of public pages and authorized data sources.

## Status

- Current version: `5.2.0`
- Test baseline: `101 passed, 1 skipped`
- Primary MCP server: `unified_crawler_server.py`
- Package name: `crawpapa-fetch`
- CLI commands:
  - `crawpapa-fetch`
  - `crawpapa-setup-clients`
  - legacy aliases: `crawler-mcp`, `crawler-setup-clients`

## Core Workflow

Recommended analysis chain:

```text
probe_access_strategy
  -> fetch_best_page
  -> observe_browser_network
  -> infer_pagination_strategy
  -> analyze_detail_samples
  -> scout_page
  -> draft_collection_plan
  -> validate_collection_plan
  -> export_site_spec_to_spider or execute_collection_plan
```

For pre-crawl analysis, you usually stop at `validate_collection_plan` or `export_site_spec_to_spider` and pass the result to your own crawler framework.

## Key MCP Tools

Access and rendering:

- `probe_access_strategy`
- `fetch_best_page`
- `fetch_page`
- `fetch_page_browser`
- `fetch_pages_batch`
- `observe_browser_network`
- `diagnose_access_strategy`
- `set_proxy`

Page understanding:

- `scout_page`
- `extract_initial_state`
- `compare_menu_sources`
- `infer_category_tree`
- `infer_site_selectors`
- `infer_site_spec_from_samples`
- `extract_structured_data`

Pagination and detail analysis:

- `infer_pagination_strategy`
- `analyze_detail_samples`
- `crawl_list`
- `crawl_product`

Plan and export:

- `draft_collection_plan`
- `validate_collection_plan`
- `execute_collection_plan`
- `draft_site_spec`
- `validate_site_spec`
- `export_site_spec_to_spider`

Data quality:

- `normalize_job_records`
- `save_data`
- `save_to_db`
- `save_batch_to_db`
- `query_db`

## Installation

### Linux and macOS

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
chmod +x install.sh start.sh pack.sh
./install.sh
./start.sh
```

Manual install:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[full,dev]"
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_mcp_clients.py
.venv/bin/python -m pytest -q
```

Start the server:

```bash
.venv/bin/python unified_crawler_server.py
```

See [Linux and macOS Installation](docs/INSTALL_UNIX.md) for system dependencies, Playwright notes, and packaging commands.

### Windows PowerShell

```powershell
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,dev]"
.\.venv\Scripts\playwright.exe install chromium
.\.venv\Scripts\python.exe setup_mcp_clients.py
.\.venv\Scripts\python.exe -m pytest -q
```

Start the server:

```powershell
.\.venv\Scripts\python.exe unified_crawler_server.py
```

Or after installation:

```powershell
crawpapa-fetch --server crawler
```

## Packaging

Build Python distribution artifacts and a portable zip:

```powershell
.\pack.bat
```

Linux/macOS:

```bash
./pack.sh
```

Equivalent Python command:

```powershell
.\.venv\Scripts\python.exe tools\maintenance\build_package.py
```

Outputs are written to `dist/`.

The packaging flow runs a secret audit first:

```powershell
.\.venv\Scripts\python.exe tools\maintenance\secret_audit.py
```

## Project Layout

```text
crawler_core/                  reusable crawler engine modules
unified_crawler_server.py      MCP tool registration and server entry
agents/                        optional agent orchestration integrations
tools/                         operator scripts and maintenance tools
workspace/                     local experiments and scratch work
tests/                         automated tests
tests/reports/                 manual task reports and evaluations
docs/                          setup, maintenance, integration, and architecture docs
output/                        generated exports only
cache/ cookies/ databases/
frontier/ jobs/ logs/          runtime state, ignored except .gitkeep
```

See [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Linux and macOS Installation](docs/INSTALL_UNIX.md)
- [Setup](docs/SETUP.md)
- [Tool Guide](docs/TOOL_GUIDE.md)
- [Packaging](docs/PACKAGING.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Maintenance](docs/MAINTENANCE.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Compliance Boundary

Crawpapa-Fetch is built for public and authorized collection analysis. It will not intentionally bypass CAPTCHA, login walls, access controls, or private network protections. When a target returns a challenge or requires authorization, the tools should report that condition and recommend permitted alternatives such as official APIs, authorized cookies, lower rate limits, or manual review.

## License

MIT. See [LICENSE](LICENSE).
