# Crawpapa-Fetch v5.5 Release Notes

## Theme

v5.5 adds a stable visualization handoff layer. Crawpapa-Fetch still focuses on crawler analysis and data readiness, but it can now package results for a downstream visualization MCP, dashboard, or report generator.

## Added

- `prepare_visualization_payload`
  - Accepts CSV/JSON records, local CSV/JSON files, SQLite tables, and `analyze_site_for_crawl` output.
  - Emits dataset metadata, inferred schema, field roles, quality statistics, suggested charts, preview records, and analysis lineage.
  - Can save the payload as JSON under `output/`.

- `validate_visualization_payload`
  - Checks the handoff contract.
  - Reports missing fields, availability flags, field role counts, type counts, and chart readiness.

- `crawler_core.visualization`
  - Moves the payload builder and contract validator into reusable core code.
  - Keeps `unified_crawler_server.py` as a thin MCP adapter.

## Why This Matters

The previous practical tests showed that Crawpapa-Fetch can help collect and clean data, but downstream reporting still needed manual interpretation. v5.5 closes that gap by creating a predictable JSON contract that another MCP or agent can consume without guessing field meaning.

## Influences From `spider_text`

The local `spider_text` / fnspider sample reinforced three patterns:

- staged data flow is easier to operate than one large opaque function
- validation should happen at explicit boundaries
- downstream tools need stable contracts, not just raw scraped rows

v5.5 applies those ideas to the analysis-to-visualization boundary.

## Tests

- Added CSV payload generation coverage.
- Added SQLite table payload generation coverage.
- Added analysis-report sample payload coverage.
- Added contract mismatch validation coverage.
