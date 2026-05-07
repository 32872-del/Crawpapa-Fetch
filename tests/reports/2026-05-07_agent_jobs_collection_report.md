# Agent Jobs Collection Test Report

Date: 2026-05-07

## Test Goal

Use the Crawpapa-Fetch as a pre-crawl analysis and extraction assistant to collect public Agent/AI Agent development job postings from online recruitment sources.

Required output fields:

- title
- location
- salary or benefits
- source channel
- description or requirements

Additional traceability fields were kept:

- url
- fetch_status

## Scope

Collected from publicly accessible pages only. The test did not bypass CAPTCHA, login walls, robots.txt restrictions, or access controls.

Covered sources:

- Ashby
- 远程工作者
- 职坐标
- 高校人才网
- 峨眉山人才网
- 牛客网
- 圆才网
- DTNS 官网

Skipped or blocked sources:

- BOSS 直聘: robots.txt disallow observed during MCP probe
- 拉勾: challenge/captcha observed during MCP probe
- 智联招聘: challenge/empty shell observed during MCP probe

## Output Artifacts

- Raw cleaned CSV: `output/agent_developer_jobs.csv`
- Collection summary: `output/agent_developer_jobs_summary.json`
- Normalized CSV: `output/agent_developer_jobs_normalized.csv`
- Quality report: `output/agent_developer_jobs_quality_report.json`
- Collection script: `tools/data_tasks/collect_agent_jobs.py`
- Normalization script: `tools/data_tasks/normalize_agent_jobs.py`

## Result Summary

Raw cleaned records: 13

Source distribution:

| Source | Records |
|---|---:|
| Ashby | 6 |
| 远程工作者 | 1 |
| 职坐标 | 1 |
| 高校人才网 | 1 |
| 峨眉山人才网 | 1 |
| 牛客网 | 1 |
| 圆才网 | 1 |
| DTNS 官网 | 1 |

Quality grades after normalization:

| Grade | Count | Meaning |
|---|---:|---|
| A | 4 | Structured source, salary/location/description mostly complete |
| B | 6 | Parsed source with usable content but partial fields |
| C | 2 | Public fallback data, should be manually verified before analysis |
| D | 1 | Low-confidence record, incomplete key fields |

Normalization coverage:

- Structured salary: 8 / 13
- Structured location: 11 / 13

## What Worked

- `curl_cffi` successfully accessed several Ashby job pages and extracted `JobPosting` JSON-LD.
- The pipeline preserved source URL and fetch status, making quality grading possible.
- The normalization pass converted mixed salary/location strings into analysis-oriented fields.
- Compliance boundaries were respected for robots-blocked or challenge-protected platforms.

## Issues Found

### 1. Auto Mode Can Regress

For Ashby, direct `curl_cffi` fetched complete HTML with JSON-LD, while `auto` sometimes escalated into browser mode and hit challenge content.

Impact:

- A simple "requests -> curl -> browser" escalation chain is not always optimal.
- MCP should compare candidate responses and choose the highest-value HTML, not just the last successful mode.

Recommended improvement:

- Add a response scoring layer:
  - presence of JSON-LD
  - body length
  - challenge/captcha signals
  - JS shell detection
  - HTTP status
  - expected selector hits

### 2. Description Noise

Some Chinese job pages included footer text, ICP records, related jobs, company introductions, and recommendation blocks.

Impact:

- Keyword extraction and JD analysis will be distorted without a cleaning layer.

Recommended improvement:

- Add reusable `clean_job_description` logic.
- Remove common footer/legal markers.
- Stop extraction at "related jobs", "company info", "copyright", "ICP备", and similar markers.

### 3. Schema Was Not Analysis-Ready Initially

Raw salary and location values mixed currencies, languages, periods, and free-form benefit text.

Impact:

- Raw CSV is readable but not directly suitable for salary distribution or regional analysis.

Recommended improvement:

- Add formal job record schema:
  - `title_raw`
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
  - `publish_date`
  - `fetch_time`
  - `quality_grade`

### 4. Collection Depth Is Still PoC-Level

13 records are enough for a capability test, but not enough for market analysis.

Impact:

- The dataset is not representative of the Agent developer job market.

Recommended improvement:

- Build source-specific collectors for accessible job boards.
- Prefer official public APIs and structured ATS sources where available.
- Target 100+ verified records before building visual analysis or conclusions.

## Assessment

This test passes as a proof of concept for MCP-assisted collection and analysis preparation.

It does not yet pass as a decision-grade data product because collection depth, source coverage, and description cleaning need further work.

## Next Actions

P0:

- Promote `normalize_agent_jobs.py` concepts into reusable MCP-side normalization tools. Status: done via `normalize_job_records` and `crawler_core/job_normalization.py`.
- Add response scoring to access strategy selection. Status: done as `fetch_best_page`.
- Add quality grading and standardized job schema.

P1:

- Expand accessible public recruitment sources.
- Add source-specific parsing plans for high-value domains.
- Add publish date extraction where available.

P2:

- Add repeatable task templates for job collection.
- Add regression fixtures for noisy job pages.
- Add dataset versioning for repeated runs.

## Follow-Up Implementation

Implemented on 2026-05-07:

- Added MCP tool `fetch_best_page`.
  - Compares requested fetch modes by response quality.
  - Rewards JSON-LD, `JobPosting`, initial state signals, and target selector hits.
  - Penalizes challenge pages, JS shells, failed fetch JSON, and very short responses.
- Added MCP tool `normalize_job_records`.
  - Converts raw job rows into analysis-ready fields.
  - Adds salary parsing, location normalization, description cleanup, category normalization, and `quality_grade`.
- Added core module `crawler_core/job_normalization.py`.
- Added regression tests:
  - `test_score_fetch_candidate_prefers_structured_jobposting`
  - `test_normalize_job_records_tool_outputs_analysis_schema`

Validation:

```text
101 passed, 1 skipped
```
