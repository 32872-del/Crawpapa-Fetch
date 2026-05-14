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
- Adaptive selector storage pathing under project databases.
- Tool and project docs for Scrapling-powered analysis.

### Fixed

- Static Scrapling fetch no longer depends on the browser-only import path.
- Parser and fetch tests now cover the vendored integration path.

### Notes

- Dynamic fetch remains analysis-only and still depends on browser extras.
- No CAPTCHA solving or login bypass was added.
