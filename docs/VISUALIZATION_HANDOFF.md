# Visualization Handoff Interface

This document defines the planned interface between Crawpapa-Fetch and a future visualization MCP, dashboard, or reporting layer.

## Design Position

Crawpapa-Fetch should stay focused on crawler analysis, data readiness, and structured outputs.

Visualization should be a downstream consumer:

```text
Crawpapa-Fetch
  -> access diagnostics
  -> structure discovery
  -> field quality
  -> schema recommendation
  -> visualization payload

Visualization MCP / Dashboard
  -> charts
  -> dashboards
  -> interactive exploration
  -> stakeholder reports
```

This keeps project boundaries clear while preserving a clean integration path.

## Planned Tool

```text
prepare_visualization_payload(
  records="",
  input_path="",
  db_name="",
  table="",
  dataset_name="",
  source_url="",
  analysis_json=""
)
```

The tool should not render charts. It prepares a stable JSON payload for another MCP, a dashboard, or a later renderer.

## Supported Inputs

Planned input sources:

- CSV file
- JSON file
- JSON records string
- SQLite table
- `normalize_job_records` output
- `analyze_site_for_crawl` output
- task output files under `output/`

## Payload Shape

```json
{
  "version": "1.0",
  "dataset": {
    "name": "qq_music_new_albums",
    "source": "https://y.qq.com/",
    "generated_at": "2026-05-07T17:06:40+08:00",
    "records_count": 488
  },
  "schema": {
    "fields": [
      {
        "name": "category",
        "type": "category",
        "role": "dimension"
      },
      {
        "name": "release_date",
        "type": "date",
        "role": "dimension"
      },
      {
        "name": "name",
        "type": "text",
        "role": "label"
      }
    ]
  },
  "quality": {
    "missing_rate": {
      "name": 0.0,
      "author": 0.0,
      "release_date": 0.0
    },
    "duplicate_rate": 0.01
  },
  "suggested_charts": [
    {
      "type": "bar",
      "title": "Records by Category",
      "x": "category",
      "y": "count"
    },
    {
      "type": "line",
      "title": "Records by Date",
      "x": "release_date",
      "y": "count"
    }
  ],
  "records_preview": [
    {
      "category": "Mainland",
      "name": "Example Album",
      "author": "Example Artist",
      "release_date": "2026-05-07"
    }
  ],
  "handoff": {
    "preferred_consumer": "visualization_mcp",
    "format": "json",
    "notes": [
      "Suitable for category and time-series charts."
    ]
  }
}
```

## Field Inference Rules

The payload builder should infer field type and visualization role:

| Field Pattern | Type | Role |
|---|---|---|
| `date`, `time`, `published_at`, `release_date` | `date` | dimension |
| `category`, `section`, `source`, `location` | `category` | dimension |
| `price`, `heat`, `score`, `count`, `salary_min`, `salary_max` | `number` | metric |
| `title`, `name`, `author`, `description` | `text` | label |
| `url`, `image`, `image_src` | `url` | metadata |

## Suggested Chart Rules

Initial chart recommendations:

- Bar chart for category counts.
- Line chart for date/time counts.
- Histogram for numeric distributions such as price or heat.
- Table preview for text-heavy datasets.
- Scatter plot when two numeric metrics exist.

## Relationship To Current Reports

`analyze_site_for_crawl` already emits:

- `site_profile`
- `field_quality`
- `recommended_schema`
- `markdown_report`

`prepare_visualization_payload` should reuse those fields when `analysis_json` is provided.

## Future Optional Renderer

If Crawpapa-Fetch later embeds visualization, it should be a second-stage tool:

```text
render_visualization_report(payload, output="html")
```

The renderer should consume the same payload. That keeps the handoff interface stable whether visualization is external or built in.

## Implementation Priority

Recommended order:

1. Implement `prepare_visualization_payload`.
2. Add tests for CSV, JSON, SQLite, and analysis-report inputs.
3. Add chart suggestion tests.
4. Add `render_visualization_report` only after the payload contract is stable.
