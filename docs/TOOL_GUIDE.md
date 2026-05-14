# Tool Guide

This guide describes the main Crawpapa-Fetch MCP tools from an operator perspective.

## Access Strategy

### `build_site_model`

Builds a compact Agent-facing model from runtime evidence.

Use it when the Agent needs a crawler implementation blueprint, not a long human report.

It wraps the main analysis chain and returns:

- `site_model.access`: access class, best mode, API/network hint counts
- `site_model.best_data_source`: preferred DOM/API/network source
- `site_model.data_sources`: ranked data source candidates
- `site_model.interaction_map`: pagination, network pagination, and category navigation hints
- `site_model.pagination`: recommended pagination strategy and sample URLs
- `site_model.category_strategy`: menu candidates and category URLs
- `site_model.detail_strategy`: list selector, detail fields, samples, and risks
- `site_model.crawler_plan`: executable crawler-plan skeleton
- `site_model.next_actions`: what the Agent should do next

This is the preferred entry point for Agent coding workflows.

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

### `observe_interactions`

Observes runtime interactions and returns action-level evidence.

It safely performs:

- initial page load
- scroll actions
- optional next/load-more click candidates

It reports:

- `actions`: each action, URL before/after, DOM delta, and new requests
- `interaction_map`: compact action summaries for Agents
- `network`: ranked request candidates across the run

Use it when a site only reveals data after scroll, next-page clicks, or load-more interactions.

It does not submit forms, bypass login, solve CAPTCHA, or force access controls.

### `infer_data_api`

Infers the shape of a public JSON API response.

Use it after `observe_browser_network`, `observe_interactions`, or script API-hint discovery.

Inputs can be:

- `url`: one JSON endpoint to fetch and inspect
- `candidate_urls`: newline, comma, JSON list, or candidate objects from network observation
- `sample_json`: already captured JSON text

It returns:

- `api_model.item_array.path`: likely list array path, such as `items` or `data.products`
- `api_model.field_paths`: likely `title`, `price`, `image`, `url`, `description`, and `id` paths inside each item
- `api_model.pagination`: `page`, `limit`, `cursor`, `total`, `hasNext`, and similar response fields
- `recommendations[0].action`: usually `implement_api_crawler`, `sample_more_api_responses`, or `manual_api_review`

Use this when the Agent needs to turn runtime network evidence into concrete crawler code.

### `infer_pagination_strategy`

Identifies:

- `rel=next`
- next text links
- query page parameters
- offset/cursor hints
- sample next-page URLs

## Sitemap And Detail-First Ecommerce

For ecommerce sites, do not assume the visible list page is the best extraction source.

If homepage or category pages look like JavaScript shells, shallow landing pages, region-specific pages, or noisy marketing pages, run:

```text
infer_category_tree(url)
parse_sitemap(product_sitemap_url)
analyze_detail_samples(product_or_category_url)
```

Strong signals for a sitemap/detail-first strategy:

- `infer_category_tree.coverage.product_sitemap_count` is high
- product URLs are available from robots/sitemap discovery
- detail pages expose Product JSON-LD or Open Graph product meta
- detail pages expose platform config such as Magento/Hyva swatch options
- list-page selectors are weak, empty, or mostly decorative

In this pattern, the Agent should build the crawler around:

- product sitemap URL discovery
- detail-page structured data extraction
- product-attribute rows for fields such as color/material
- platform variant config for sizes and availability
- image filtering that keeps only product media URLs

## Page Understanding

### `scout_page`

High-level page reconnaissance. It combines access diagnostics, links, selector candidates, initial-state hints, and recommendations.

### `extract_initial_state`

Reads frontend initial state JSON from HTML. Useful for ecommerce navigation and menu trees.

Example path:

```text
navigation.multiBrandMenu[0].mainMenu
```

When `output_format` is `tree` or `dict`, the result also includes `directory_profile`:

- `business_score`: practical confidence that the source is a real business/category directory
- `max_depth` and `avg_depth`: whether the menu is hierarchical
- `url_coverage`: how many retained nodes have usable URLs
- `valid_ratio`: retained nodes compared with filtered/noisy nodes
- `url_type_counts`: category, content, promotion, product, external, or missing URL classes
- `signals`: short hints such as `hierarchical`, `category_url_dominant`, and `low_filter_noise`

### `compare_menu_sources`

Compares multiple menu candidates and reports recommended source, differences, and filtering reasons.

Use it when a site exposes several possible menu structures, such as desktop menus, mobile menus, `navigation.mainMenu`, and `navigation.multiBrandMenu[*].mainMenu`.

Each matched source includes:

- `directory_profile`
- `score`
- `explanation`
- `filter_report`
- optional tree/dict output

The top-level `diff_summary` compares the recommended source with the other matched sources:

- `recommended_title_coverage` and `recommended_url_coverage`
- `only_in_recommended`
- `missing_from_recommended`
- `shared_titles`
- per-source `title_overlap_ratio` and `url_overlap_ratio`
- `warnings` when the recommended source misses many titles or URLs from other sources

The recommendation favors sources with strong business-directory signals, usable URLs, useful depth, and low hidden/content/external noise.

### Scrapling-powered tools

`scrapling_status`, `scrapling_parse`, `scrapling_find_similar`, `scrapling_fetch`,
`scrapling_spider_status`, and `scrapling_spider_run` are vendored from Scrapling 0.4.8
and live inside this repo.

Use them when you need:

- CSS/XPath parsing with adaptive selector relocation
- seed-element expansion into structurally similar cards
- static fetches that preserve Scrapling's parser metadata
- a quick check on optional parser/browser dependencies
- real site-level crawls with scheduler priority, URL deduplication, robots.txt checks, sitemap seeds, checkpoint directories, and follow rules

Notes:

- These tools are analysis-oriented.
- They do not expose CAPTCHA solving or login bypass.
- `scrapling_fetch` supports static and dynamic page retrieval, but dynamic mode depends on browser-related extras.
- `scrapling_spider_run` defaults to static `FetcherSession`; it is intended to prove crawl strategy and extraction shape before a production crawler is written.

### `scrapling_spider_run`

Runs a JSON-defined Scrapling `CrawlSpider` or `SitemapSpider` without asking the operator
to write a Python subclass.

Minimal crawl spec:

```json
{
  "name": "example_products",
  "spider_type": "crawl",
  "start_urls": ["https://example.com/category"],
  "item_selector": "article.product-card",
  "item_fields": {
    "title": ".product-title",
    "url": "a@href",
    "price": ".price"
  },
  "follow_rules": [
    {
      "allow": ["/product/"],
      "restrict_css": "article.product-card",
      "callback": "parse_detail",
      "priority": 10
    }
  ],
  "max_depth": 1,
  "max_items": 100,
  "robots_txt_obey": true
}
```

Minimal sitemap spec:

```json
{
  "name": "example_sitemap_products",
  "spider_type": "sitemap",
  "sitemap_urls": ["https://example.com/sitemap-products.xml"],
  "item_fields": {
    "title": "h1",
    "price": ".price",
    "description": ".description"
  },
  "max_items": 100
}
```

Field selectors can be strings such as `.price`, `a@href`, `img@src`, or objects:

```json
{
  "image_urls": {"selector": ".gallery img@src", "many": true},
  "title": {"selector": "h1", "required": true}
}
```

The response includes:

- extracted `items`
- `stats` from Scrapling's engine, including request count, offsite count, robots disallow count, cache hits/misses, status counts, and bytes
- `spec_summary` showing which sources, fields, rules, depth, and checkpoint settings were used
- optional JSON artifact and optional SQLite save result

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

### `prepare_visualization_payload`

Prepares a stable JSON payload for a future visualization MCP, dashboard, or report renderer.

Supported inputs:

- CSV/JSON text via `records`
- CSV/JSON files via `input_path`
- SQLite tables via `db_name` and `table`
- `analyze_site_for_crawl` output via `analysis_json`

It emits:

- dataset metadata
- inferred schema
- field roles
- missing and duplicate rates
- suggested charts
- records preview
- contract validation and availability report

See [Visualization Handoff Interface](VISUALIZATION_HANDOFF.md).

### `validate_visualization_payload`

Validates the visualization handoff contract and reports:

- missing required top-level fields
- schema field contract issues
- role/type counts
- chart readiness
- whether the payload has records, metrics, dimensions, labels, and preview data

### `target_memory_stats`

Shows persistent target-analysis memory.

Use it to inspect:

- remembered target profiles
- preferred sources and modes
- menu source paths
- list/pagination hints
- evidence snapshots and analysis summaries

### `target_memory_get`

Reads one stored target profile by `target_name`, optional `source_url`, and optional `target_type`.

### `target_memory_reset`

Deletes one stored target profile by the same key fields.

### `target_memory_stats`

Shows the current target-memory inventory, grouped by target type.

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
