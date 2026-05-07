# Roadmap

## Current Focus

Crawpapa-Fetch is moving from a capable single-server MCP prototype toward a professional open-source crawler analysis platform.

## Near-Term Priorities

### P0: Engineering Foundation

- Keep CI green on supported Python versions.
- Keep `secret_audit.py` in the release workflow.
- Continue moving reusable logic from `unified_crawler_server.py` into `crawler_core/`.
- Preserve existing MCP tool names while refactoring internals.

### P1: Access Strategy Intelligence

- Integrate `fetch_best_page` scoring into `_smart_fetch(auto)`.
- Store per-domain winning strategies with confidence and failure reasons.
- Add response fixtures for challenge pages, JS shells, compressed pages, and structured HTML.

### P2: Structured Extraction Quality

- Generalize `normalize_job_records` into schema-oriented normalization.
- Add reusable cleaners for ecommerce, jobs, articles, and category trees.
- Add confidence scores and data lineage to extraction outputs.

### P3: Site Analysis Playbooks

- Add playbooks for common site families:
  - ecommerce catalog/detail pages
  - ATS/job board pages
  - article/news sites
  - public service directories
- Export stable crawl plans and site specs for downstream crawler frameworks.

### P4: Operator Experience

- Improve docs and examples.
- Add sample MCP client configurations.
- Add optional dashboard/report generation for collection tests.

### P5: Visualization Handoff

- Add `prepare_visualization_payload` as a stable JSON handoff interface.
- Support CSV, JSON, SQLite table, normalized records, and analysis-report inputs.
- Infer visualization field roles: dimension, metric, label, metadata.
- Suggest charts without rendering them inside the crawler core.
- Keep optional rendering as a later second-stage tool.

## Non-Goals

- CAPTCHA cracking.
- Credential theft or account abuse.
- Bypassing private network protections.
- Ignoring robots.txt or target terms.
- Turning Crawpapa-Fetch into a full dashboard product before the handoff contract is stable.
