# Scrapling Spider Runner Logic

`scrapling_spider_run` turns an Agent-readable JSON spec into a temporary in-memory
Scrapling spider. It is designed for crawl strategy validation: prove the queue,
rules, sitemap, robots, checkpoint, and extraction shape before a production crawler
is written.

```mermaid
flowchart TD
  A["Agent JSON spec"] --> B["MCP tool: scrapling_spider_run"]
  B --> C["Validate spec shape and target URLs"]
  C --> D["Build field specs and follow rules"]
  D --> E{"spider_type"}
  E -->|"crawl"| F["Dynamic CrawlSpider subclass"]
  E -->|"sitemap"| G["Dynamic SitemapSpider subclass"]
  F --> H["FetcherSession static HTTP client"]
  G --> H
  H --> I["CrawlerEngine"]
  I --> J["Scheduler: priority queue and dedup"]
  I --> K["RobotsTxtManager when enabled"]
  I --> L["CheckpointManager when crawldir is enabled"]
  I --> M["Response cache in development mode"]
  J --> N["Fetch response"]
  K --> N
  N --> O["Extract fields with CSS/XPath"]
  N --> P["Apply LinkExtractor follow rules"]
  P --> J
  O --> Q["Items and engine stats"]
  Q --> R["MCP v5 envelope"]
  R --> S["Optional JSON artifact"]
  R --> T["Optional SQLite save"]
```

## Spec Inputs

- `spider_type`: `crawl` or `sitemap`
- `start_urls` or `sitemap_urls`
- `allowed_domains`: optional; seed domains are added automatically, including `host:port`
- `item_selector`: optional repeated item scope
- `item_fields`: CSS/XPath field map
- `follow_rules`: optional link extraction rules with `allow`, `deny`, `restrict_css`, `restrict_xpath`, `callback`, and `priority`
- `max_depth`, `max_items`, `concurrent_requests`, `download_delay`
- `robots_txt_obey`, `use_checkpoint`, `crawldir`, `development_mode`

## Output Contract

- `items`: extracted records with `_source_url`
- `stats`: Scrapling engine stats, including requests, offsite filters, robots blocks, cache hits/misses, status counts, and bytes
- `spec_summary`: normalized sources, domains, fields, rules, depth, and checkpoint settings
- `artifact_path`: optional JSON output path
- `db_result`: optional SQLite save result

## Boundaries

- No CAPTCHA solving.
- No login-wall bypass.
- Private/local targets are blocked unless the caller explicitly enables trusted `allow_private`.
- Stealth browser dependencies may exist in the vendored source, but this runner defaults to static `FetcherSession`.
