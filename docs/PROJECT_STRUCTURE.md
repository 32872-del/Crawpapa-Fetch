# Project Structure

This project is organized around a stable MCP runtime, reusable tooling, runtime state, and test/report artifacts.

## Top-Level Areas

```text
Crawpapa-Fetch/
  unified_crawler_server.py      # Current MCP server entry and tool registry
  main.py                        # Console entry wrapper
  crawler_core/                  # Reusable crawler engine modules
  agents/                        # Agent orchestration experiments/integration code
  config/                        # Configuration helpers and defaults
  utils/                         # Small shared utilities
  tools/                         # Operator/dev scripts that are not MCP runtime code
  workspace/                     # Local experiments, scratch files, and temporary working sets
  tests/                         # Automated tests
  tests/reports/                 # Manual test reports and task evaluations
  .github/workflows/             # CI workflows
  docs/                          # User, maintenance, integration, and architecture docs
  schemas/                       # Data schemas used by save/query tools
  templates/                     # Reusable crawl pipeline templates
  output/                        # Generated exports, reports, and one-off task outputs
  cache/ cookies/ databases/
  frontier/ jobs/ logs/          # Runtime crawler state
```

## Directory Responsibilities

### Runtime Code

- `unified_crawler_server.py`
  - Current MCP server implementation.
  - Keep this runnable while refactoring.
  - New large capabilities should gradually move into `crawler_core/` and be registered from the server.

- `crawler_core/`
  - Stable modules for fetching, parsing, diagnostics, frontier, templates, security, and site specs.
  - Put reusable business logic here when it is part of the MCP product.

- `agents/`
  - Agent-side orchestration and integration experiments.
  - Do not place core crawler behavior here unless it is intentionally agent-specific.

### Tools

- `tools/data_tasks/`
  - Reusable scripts for concrete data tasks, normalization, evaluation, and offline transforms.
  - Scripts here may consume MCP tools or outputs, but they are not registered as MCP tools.

- `tools/maintenance/`
  - Project maintenance scripts such as cleanup, migration, release checks, or data pruning.
  - `secret_audit.py` must pass before release.
  - `build_package.py` builds portable zip and optional Python distribution artifacts.

### Workspace

- `workspace/experiments/`
  - Temporary experiments and prototypes that may become tools or MCP features later.

- `workspace/scratch/`
  - Throwaway local files.
  - Do not rely on contents here for tests or releases.

### Tests

- `tests/`
  - Automated unit/integration tests.
  - Test fixtures should be small and deterministic.

- `tests/reports/`
  - Human-readable test briefings, task evaluations, and acceptance notes.
  - Use this for real-world task reports that are not automated tests.

### Runtime State And Outputs

- `output/`
  - Generated task outputs only: CSV, JSON reports, HTML captures, screenshots.
  - Do not keep scripts here.

- `cache/`, `cookies/`, `databases/`, `frontier/`, `jobs/`, `logs/`
  - Runtime state. These are local and usually gitignored.
  - Keep `.gitkeep` only if the empty directory needs to exist after clone.

## Naming Rules

- Task output prefix: `<site_or_task>_<purpose>.<ext>`
  - Example: `agent_developer_jobs.csv`
  - Example: `amazon_pagination_strategy.json`

- Test reports: `YYYY-MM-DD_<topic>_report.md`
  - Example: `2026-05-07_agent_jobs_collection_report.md`

- Tools: verb-oriented names
  - Example: `collect_agent_jobs.py`
  - Example: `normalize_agent_jobs.py`

## Refactor Policy

Keep refactors staged:

1. Move task scripts out of `output/`.
2. Document ownership boundaries.
3. Add tests before moving runtime logic.
4. Extract one MCP capability at a time from `unified_crawler_server.py` into `crawler_core/`.
5. Verify with `python -m pytest -q` after each stage.

