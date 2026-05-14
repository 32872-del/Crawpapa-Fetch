# Crawpapa-Fetch

Crawpapa-Fetch is an agent-oriented crawler analysis MCP server. It helps LLMs and operators understand a website before writing or running a production crawler.

It is designed for:

- Access diagnostics across `requests`, `curl_cffi`, browser rendering, proxies, and authorized cookies.
- Page structure discovery: selectors, initial-state JSON, navigation trees, script URLs, API hints, and structured data.
- Pagination and detail-page scouting.
- Crawl plan drafting, validation, and export.
- Site type detection, field quality scoring, Markdown reports, and recommended schemas.
- Structured data quality checks such as job record normalization and quality grading.
- Visualization handoff payloads for downstream dashboards, reporting tools, or another MCP.

Crawpapa-Fetch is not a CAPTCHA cracker, account bypass tool, or stealth abuse framework. It is intended for lawful analysis of public pages and authorized data sources.

## Status

- Current version: `5.4.3`
- Test baseline: `123 passed, 1 skipped`
- Primary MCP server: `unified_crawler_server.py`
- Package name: `crawpapa-fetch`
- CLI commands:
  - `crawpapa-fetch`
  - `crawpapa-setup-clients`
  - legacy aliases: `crawler-mcp`, `crawler-setup-clients`

## Core Workflow

Recommended analysis chain:

```text
build_site_model
```

Or, if you want to inspect each stage yourself:

```text
probe_access_strategy
  -> fetch_best_page
  -> observe_browser_network
  -> observe_interactions
  -> infer_data_api
  -> infer_pagination_strategy
  -> infer_category_tree
  -> analyze_detail_samples
  -> scout_page
  -> draft_collection_plan
  -> validate_collection_plan
  -> export_site_spec_to_spider or execute_collection_plan
```

For pre-crawl analysis, you usually stop at `validate_collection_plan` or `export_site_spec_to_spider` and pass the result to your own crawler framework.

For ecommerce targets, check public sitemap and detail-page evidence early. If `infer_category_tree` finds strong product sitemap coverage and detail pages expose Product JSON-LD, Open Graph product meta, or platform variant config, prefer a sitemap-to-detail crawler over fragile list-page selectors.

`scrapling_*` tools are vendored into this repo from Scrapling 0.4.8 and add parser-level resilience:

- `scrapling_status` for dependency and feature checks
- `scrapling_parse` for CSS/XPath parsing with adaptive selector storage
- `scrapling_find_similar` for sibling-card discovery from one seed element
- `scrapling_fetch` for static or browser-backed page retrieval
- `scrapling_spider_status` for spider framework, scheduler, robots, sitemap, checkpoint, and cache checks
- `scrapling_spider_run` for JSON-driven CrawlSpider/SitemapSpider runs with follow rules and structured field extraction

They are analysis tools, not CAPTCHA-bypass or login-wall bypass features.

## Key MCP Tools

Access and rendering:

- `analyze_site_for_crawl`
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
- `scrapling_status`
- `scrapling_parse`
- `scrapling_find_similar`
- `scrapling_fetch`
- `scrapling_spider_status`
- `scrapling_spider_run`

Pagination and detail analysis:

- `infer_pagination_strategy`
- `analyze_detail_samples`
- `scrapling_spider_run`
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

- `detect_site_type`
- `field_quality_report`
- `generate_site_report`
- `prepare_visualization_payload`
- `validate_visualization_payload`
- `target_memory_stats`
- `target_memory_get`
- `target_memory_reset`
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

Run a pre-crawl report from the CLI:

```bash
crawpapa-fetch analyze https://example.com/products --goal product_list --output-file report.json
crawpapa-fetch analyze https://example.com/products --goal product_list --report-format markdown --output-file report.md
crawpapa-fetch diagnose https://example.com/products
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
scrapling/                     vendored Scrapling 0.4.8 source
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
- [Visualization Handoff](docs/VISUALIZATION_HANDOFF.md)
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
