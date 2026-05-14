# Release Notes

## Crawpapa-Fetch 5.4.2

This release internalizes Scrapling 0.4.8 into the Crawpapa-Fetch codebase and exposes it as first-class MCP analysis tools.

### Added

- Vendored `scrapling/` source tree with BSD-3-Clause notice.
- New MCP tools:
  - `scrapling_status`
  - `scrapling_parse`
- `scrapling_find_similar`
- `scrapling_fetch`
- `scrapling_spider_status`
- `scrapling_spider_run`
- Adaptive selector storage pathing under project databases.
- Tool and project docs for Scrapling-powered analysis.
- JSON-driven Scrapling spider runner with crawl/sitemap support, follow rules, item extraction, robots.txt handling, and checkpoint-ready config.
- Architecture logic diagram for the Scrapling spider runner.

### Fixed

- Static Scrapling fetch no longer depends on the browser-only import path.
- Parser and fetch tests now cover the vendored integration path.
- Spider package import now works without forcing optional stealth browser dependencies.

### Notes

- Dynamic fetch remains analysis-only and still depends on browser extras.
- No CAPTCHA solving or login bypass was added.
- `scrapling_spider_run` is meant to help the Agent reason about site structure and crawl strategy before writing a production crawler.
