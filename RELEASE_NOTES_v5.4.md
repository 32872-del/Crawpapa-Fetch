# Crawpapa-Fetch v5.4 Release Notes

Release date: 2026-05-07

## Highlights

v5.4 upgrades Crawpapa-Fetch from crawler planning into stronger crawler analysis and data-readiness reporting.

The focus is:

- Site type detection.
- Field-level quality scoring.
- Human-readable Markdown reports.
- Recommended schemas for downstream analysis.

## New MCP Tools

- `detect_site_type`
  - Classifies ecommerce, jobs, news, directory, or unknown pages.
  - Reports page type and preferred data source when used with an analysis report.
- `field_quality_report`
  - Scores selectors and sample values field by field.
  - Flags likely price, image, description, salary, and location noise.
- `generate_site_report`
  - Converts an `analyze_site_for_crawl` JSON report into Markdown.

## Unified Analysis Improvements

`analyze_site_for_crawl` now includes:

- `site_profile`
- `field_quality`
- `recommended_schema`
- `markdown_report`
- richer `implementation_hints`

It also supports:

```text
report_format="markdown"
```

for direct human-readable output.

## CLI Improvements

The CLI now supports Markdown report output:

```bash
crawpapa-fetch analyze https://example.com/products --report-format markdown --output-file report.md
```

## Compliance

Crawpapa-Fetch still does not bypass CAPTCHA, login walls, private resources, or access controls. New reports should make those boundaries easier to see before crawler code is written.
