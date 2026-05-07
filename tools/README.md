# Tools

This directory contains operator and developer scripts that support MCP usage but are not MCP tools themselves.

## Layout

```text
tools/
  data_tasks/     # Concrete collection, cleanup, normalization, and evaluation scripts
  maintenance/    # Project cleanup, migration, and release support scripts
```

## Rules

- Put reusable scripts here instead of `output/`.
- Scripts should read from project paths and write generated artifacts to `output/`.
- Keep scripts deterministic when possible.
- If a script becomes broadly useful to agents, promote the core logic into `crawler_core/` and expose it through `unified_crawler_server.py`.

## Current Data Task Scripts

- `data_tasks/collect_agent_jobs.py`
  - Collects public Agent/AI Agent job postings from accessible sources and writes `output/agent_developer_jobs.csv`.

- `data_tasks/normalize_agent_jobs.py`
  - Normalizes the Agent job CSV into analysis-ready fields and writes `output/agent_developer_jobs_normalized.csv`.

