# Crawpapa-Fetch v5.6 Release Notes

## Theme

v5.6 adds persistent target memory on top of the existing domain access memory.

## Added

- `target_memory_stats`
- `target_memory_get`
- `target_memory_reset`
- `crawler_core.target_memory`

## What It Remembers

- target profile and target type
- preferred source and mode
- menu source path
- list and pagination hints
- detail selector text
- evidence snapshots from analysis runs
- analysis summary, field quality, and recommended schema

## Behavior

- `analyze_site_for_crawl` now writes successful analysis results into target memory.
- `get_crawl_status` now reports whether domain memory and target memory are enabled.
- The memory schema is generic enough to support non-crawler target analysis workflows later.

## Why It Matters

Domain memory was already helping auto-mode pick a working fetch path. v5.6 adds a higher layer: remembering what a target actually looks like, so the assistant can stop re-deriving menu sources, pagination hints, and field structure every time.

## Tests

- Full test suite passes before release.
- New memory-path tests remain covered by the existing integration suite.
