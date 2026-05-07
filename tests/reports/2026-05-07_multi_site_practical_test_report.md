# Crawpapa-Fetch Multi-Site Practical Test Report

Date: 2026-05-07

Version under test: Crawpapa-Fetch v5.4

## Test Goal

This practical test evaluated whether Crawpapa-Fetch can help an LLM/operator finish mixed web data tasks across static pages, dynamic pages, JavaScript-rendered pages, and product-detail extraction.

The test included three targets:

1. Weibo hot search page: `https://s.weibo.com/top/summary/`
2. QQ Music new albums: `https://y.qq.com/`
3. uvex Poland bicycle helmets: `https://uvex.com.pl/produkty/kaski-rowerowe/`

## Required Outputs

### Weibo

Required:

- Current highest hot search and 50 hot search rows.
- Sections: hot search, entertainment, life, society.
- Fields: rank, title, heat.
- Store sections in separate sheets.

Generated:

- `output/weibo_hot_search_2026-05-07.xlsx`
- `output/weibo_hot_search_2026-05-07.csv`

Result counts:

- Hot search: 51 rows
- Entertainment: 50 rows
- Life: 50 rows
- Society: 50 rows

Note: CSV does not support multiple sheets, so the multi-sheet deliverable was generated as XLSX, with a combined CSV backup.

### QQ Music

Required:

- Find new albums.
- Categories: Mainland, Hong Kong/Taiwan, Europe/US, Korea, Japan.
- Crawl up to 100 records per category.
- Fields: name, author, date.
- Store in a DB file.

Generated:

- `output/qq_music_new_albums_2026-05-07.db`

Result counts:

- Mainland: 97
- Hong Kong/Taiwan: 97
- Europe/US: 96
- Korea: 99
- Japan: 99
- Total: 488

Note: the public rendered/SSR pages exposed fewer than 100 records per category during this run. The task stored all publicly available records instead of padding or fabricating data.

### uvex Helmets

Required:

- Find 50 helmets priced above 200 PLN.
- Extract description and specification parameters.
- Generate an MD report recommending 10 products based on price and parameter advantages.

Generated:

- `output/uvex_helmets_over_200_2026-05-07.json`
- `output/uvex_helmets_over_200_2026-05-07.csv`
- `output/uvex_top10_recommendations_2026-05-07.md`

Result count:

- 50 helmets above 200 PLN
- 10 recommended helmets in Markdown report

## User Evaluation

Scores are out of 10.

| Dimension | Score | Comment |
|---|---:|---|
| Speed | 9 | Original limit was 40 minutes; actual completion was about 15 minutes, with obvious speed improvement. |
| Format | 7 | After cleaning, the outputs became much easier to inspect. |
| Assistance | 6 | The MCP helped the LLM distinguish where the required content lived. |
| Collection Depth | 7 | For the specified websites, it could autonomously collect the required content. |
| Extensibility | 7 | It supported multiple output formats including XLSX, CSV, SQLite DB, JSON, and Markdown. |

Summary from evaluator:

> The current websites may be relatively simple, so the task was completed very well. A harder practical test is recommended next time.

## What This Test Proved

1. Crawpapa-Fetch is useful as a pre-crawl analysis assistant.
   - It identified that Weibo required browser rendering.
   - It found that QQ Music new-album pages exposed usable rendered/SSR album data.
   - It helped diagnose uvex robots behavior before static extraction.

2. The workflow can handle multiple output formats in one task.
   - XLSX for multi-sheet tabular output.
   - SQLite for structured music records.
   - JSON/CSV for product datasets.
   - Markdown for recommendation reports.

3. The current analysis tooling helps choose the right extraction path.
   - Browser DOM for Weibo.
   - Rendered DOM/SSR for QQ Music.
   - Static HTML list/detail parsing for uvex.

## Lessons Learned

### 1. Output format validation should be part of planning

The user asked for CSV with separate sheets. CSV cannot contain multiple sheets. The system handled this by producing XLSX plus a CSV backup, but the MCP should flag this earlier as an output-format correction.

Suggested upgrade:

- Add output contract validation to `analyze_site_for_crawl`.
- Warn when requested format and requested structure conflict.
- Example: `multi_sheet_requested_but_csv_selected`.

### 2. Browser-rendered finite lists need availability reporting

QQ Music exposed fewer than 100 records per category. The final result was correct for available public data, but the MCP should make that limitation explicit during analysis.

Suggested upgrade:

- Add `availability_report`.
- Include requested count, discovered count, likely reason, and recommended next step.
- Example fields:
  - `requested_items`
  - `available_items`
  - `shortfall`
  - `shortfall_reason`

### 3. Robots interpretation needs better diagnostics

The MCP robots check appeared conservative on uvex because of the `Disallow: /?` rule, while the target path itself was publicly accessible and not a query URL. The final collection was performed after manually checking `robots.txt`.

Suggested upgrade:

- Add a `robots_explain` tool or richer diagnostics in `_check_robots`.
- Show matched rule, user-agent, target path, and whether query-string rules caused the block.
- Add tests for `Disallow: /?` so it does not block non-query paths incorrectly.

### 4. Field quality scoring should be used during real tasks

The uvex recommendation report relied on keyword scoring for MIPS, Inmould, ventilation, lightness, adjustment systems, LED/reflective signals, and visor terms. This logic should become reusable instead of staying task-specific.

Suggested upgrade:

- Extend `field_quality_report` with domain profiles.
- Add ecommerce product recommendation scoring helpers.
- Support weighted scoring by domain:
  - safety gear
  - electronics
  - apparel
  - music/media

### 5. Encoding safety matters on Windows

PowerShell pipeline execution initially corrupted Chinese string literals into `??`. The final run used Unicode escapes and UTF-8 output settings.

Suggested upgrade:

- Add Windows execution guidance for data scripts.
- Prefer saved UTF-8 scripts over inline PowerShell pipelines for Chinese-heavy tasks.
- Add a helper writer that emits UTF-8 task scripts safely.

## Recommended Next Upgrades

Priority order:

1. Output contract validator.
2. Availability and shortfall reporting.
3. Robots rule explanation and test coverage.
4. Reusable domain scoring profiles for recommendations.
5. Safer generated-task script runner for Windows UTF-8 workflows.

## Overall Conclusion

This practical test was successful. Crawpapa-Fetch handled three different collection patterns in one session:

- JavaScript-rendered hot search table.
- Dynamic/SSR music listing.
- Static ecommerce list/detail extraction with filtering and recommendation.

The strongest result was speed and multi-format delivery. The weakest areas were early format validation, automated shortfall explanation, and robots-rule explainability. These are good candidates for v5.5.
