# Tool Guide

This guide describes the main Crawpapa-Fetch MCP tools from an operator perspective.

## Access Strategy

### `analyze_site_for_crawl`

Runs the recommended pre-crawl workflow as one report:

```text
probe_access_strategy
fetch_best_page
observe_browser_network
infer_pagination_strategy
scout_page
analyze_detail_samples
draft_collection_plan
validate_collection_plan
```

Use it as the first tool when an Agent needs enough context to write crawler code.

Output highlights:

- `data.summary`: concise readiness and confidence summary.
- `data.site_profile`: site type, page type, and preferred data source.
- `data.field_quality`: field-level grades, scores, sample values, and risks.
- `data.recommended_schema`: field types, dedupe keys, normalization rules, and quality checks.
- `data.implementation_hints`: mode, selectors, pagination, detail fields, and risk flags.
- `data.plan`: generated collection plan.
- `data.markdown_report`: human-readable report for operators.
- `diagnostics.steps`: step-by-step execution status.
- `diagnostics.sections`: compact evidence from each lower-level tool.

Set `report_format="markdown"` to return only the Markdown report.

### `probe_access_strategy`

Compares access modes and classifies failures.

Use it when:

- A page might require browser rendering.
- A target returns 403, 429, empty HTML, JS shell, or challenge content.
- You need recommendations before writing crawler code.

Output highlights:

- `diagnostics.probes`
- `data.api_hints`
- `recommendations`

### `fetch_best_page`

Fetches the same URL through multiple modes and scores response quality.

It rewards:

- JSON-LD
- `JobPosting`
- initial-state signals
- target selector hits
- longer meaningful HTML

It penalizes:

- challenge pages
- JS shells
- short error JSON
- failed fetch results

Use it when simple auto escalation may pick the wrong response.

## Network And Pagination

### `observe_browser_network`

Uses browser rendering to observe `xhr`, `fetch`, `document`, and other response candidates.

Use it to find:

- public JSON APIs
- pagination parameters
- list/detail requests
- filter endpoints

### `infer_pagination_strategy`

Identifies:

- `rel=next`
- next text links
- query page parameters
- offset/cursor hints
- sample next-page URLs

## Page Understanding

### `scout_page`

High-level page reconnaissance. It combines access diagnostics, links, selector candidates, initial-state hints, and recommendations.

### `extract_initial_state`

Reads frontend initial state JSON from HTML. Useful for ecommerce navigation and menu trees.

Example path:

```text
navigation.multiBrandMenu[0].mainMenu
```

### `compare_menu_sources`

Compares multiple menu candidates and reports recommended source, differences, and filtering reasons.

## Detail Sampling

### `analyze_detail_samples`

Starts from a list page, extracts detail links, enters a small sample of details, and votes on field selectors.

Use this when list-page-only analysis is too shallow.

## Plans And Exports

### `draft_collection_plan`

Creates an agent-readable plan with assumptions, selectors, risk flags, and output shape.

### `validate_collection_plan`

Checks whether a plan can execute and optionally samples results.

### `export_site_spec_to_spider`

Exports a reviewed `site_spec` into a separate crawler framework folder.

## Data Quality

### `detect_site_type`

Infers whether a target looks like ecommerce, jobs, news, directory, or unknown.

It also estimates page type and preferred data source when called with a full analysis report.

### `field_quality_report`

Scores extracted fields using selector risk, non-empty ratio, value plausibility, and domain-specific noise checks.

Use it before production crawler implementation, especially for price, image, description, salary, and location fields.

### `generate_site_report`

Converts an `analyze_site_for_crawl` JSON report into a human-readable Markdown report.

### `normalize_job_records`

Normalizes job records from CSV/JSON/local file input.

Adds:

- `title_normalized`
- `job_category`
- `country`
- `province_state`
- `city`
- `is_remote`
- `currency`
- `salary_min`
- `salary_max`
- `salary_period`
- `salary_negotiable`
- `benefits`
- `description_clean`
- `quality_grade`

Use A/B rows for analysis. Review C/D rows manually before making decisions.
