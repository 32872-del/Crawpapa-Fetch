"""Visualization handoff payload helpers.

This module prepares stable, renderer-agnostic JSON payloads for dashboards,
report generators, or downstream MCP servers. It intentionally does not render
charts; it describes data readiness and reasonable visualization choices.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any


PAYLOAD_VERSION = "1.0"

DATE_FIELD_RE = re.compile(r"(date|time|published|release|created|updated|day|month|year)", re.I)
CATEGORY_FIELD_RE = re.compile(r"(category|section|source|channel|location|city|country|status|type|grade|tag)", re.I)
NUMBER_FIELD_RE = re.compile(r"(price|heat|score|count|salary|amount|total|rank|rating|views?|likes?|metric)", re.I)
URL_FIELD_RE = re.compile(r"(url|link|href|image|img|src|thumbnail|avatar)", re.I)
LABEL_FIELD_RE = re.compile(r"(title|name|author|artist|company|product|album|keyword)", re.I)
LONG_TEXT_FIELD_RE = re.compile(r"(description|body|content|requirements|summary|spec|detail|notes?)", re.I)


def load_records(records: str = "", input_path: str = "", input_format: str = "auto") -> list[dict[str, Any]]:
    """Load records from a JSON/CSV string or a JSON/CSV file."""
    raw = records
    if input_path:
        raw = Path(input_path).read_text(encoding="utf-8-sig")
    if not raw:
        return []

    fmt = (input_format or "auto").lower()
    if fmt == "auto":
        suffix = Path(input_path).suffix.lower() if input_path else ""
        stripped = raw.lstrip()
        if suffix == ".csv":
            fmt = "csv"
        elif suffix == ".json":
            fmt = "json"
        else:
            fmt = "json" if stripped.startswith("[") or stripped.startswith("{") else "csv"

    if fmt == "csv":
        return [dict(row) for row in csv.DictReader(StringIO(raw))]
    if fmt != "json":
        raise ValueError("input_format must be auto, csv, or json")

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return [_coerce_record(item) for item in parsed]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return [_coerce_record(item) for item in parsed["records"]]
        if isinstance(parsed.get("data"), dict) and isinstance(parsed["data"].get("records"), list):
            return [_coerce_record(item) for item in parsed["data"]["records"]]
        if isinstance(parsed.get("data"), list):
            return [_coerce_record(item) for item in parsed["data"]]
        return [_coerce_record(parsed)]
    raise ValueError("JSON input must be an object, array, or {records: [...]}")


def build_visualization_payload(
    records: list[dict[str, Any]] | None = None,
    *,
    dataset_name: str = "",
    source_url: str = "",
    analysis: dict[str, Any] | None = None,
    source_type: str = "records",
    preview_limit: int = 20,
) -> dict[str, Any]:
    """Build the stable visualization handoff payload."""
    analysis = _unwrap_analysis(analysis or {})
    rows = [_coerce_record(item) for item in (records or [])]
    dataset_name = dataset_name or _dataset_name_from_analysis(analysis) or "dataset"
    source_url = source_url or str(analysis.get("url") or "")
    fields = _field_order(rows, analysis)
    schema_fields = [_field_schema(field, [row.get(field) for row in rows]) for field in fields]
    quality = _quality_report(rows, fields)
    charts = _suggest_charts(schema_fields, rows)
    notes = _handoff_notes(rows, schema_fields, quality, analysis)
    payload = {
        "version": PAYLOAD_VERSION,
        "dataset": {
            "name": dataset_name,
            "source": source_url,
            "source_type": source_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "records_count": len(rows),
        },
        "schema": {
            "fields": schema_fields,
            "field_count": len(schema_fields),
        },
        "quality": quality,
        "suggested_charts": charts,
        "records_preview": rows[: max(0, int(preview_limit or 0))],
        "analysis_context": _analysis_context(analysis),
        "handoff": {
            "preferred_consumer": "visualization_mcp",
            "format": "json",
            "contract": "crawpapa.visualization_payload.v1",
            "notes": notes,
        },
    }
    payload["contract_report"] = validate_visualization_payload(payload)
    return payload


def validate_visualization_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the handoff contract and return an availability report."""
    issues: list[dict[str, Any]] = []
    required_top = ["version", "dataset", "schema", "quality", "suggested_charts", "records_preview", "handoff"]
    for key in required_top:
        if key not in payload:
            issues.append({"severity": "error", "path": key, "message": "required top-level key is missing"})

    dataset = payload.get("dataset") if isinstance(payload.get("dataset"), dict) else {}
    schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    fields = schema.get("fields") if isinstance(schema.get("fields"), list) else []
    preview = payload.get("records_preview") if isinstance(payload.get("records_preview"), list) else []

    if payload.get("version") != PAYLOAD_VERSION:
        issues.append({"severity": "warn", "path": "version", "message": f"expected {PAYLOAD_VERSION}"})
    if not dataset.get("name"):
        issues.append({"severity": "warn", "path": "dataset.name", "message": "dataset name is empty"})
    if not isinstance(dataset.get("records_count", 0), int):
        issues.append({"severity": "error", "path": "dataset.records_count", "message": "records_count must be an integer"})
    if not fields:
        issues.append({"severity": "warn", "path": "schema.fields", "message": "no fields available"})
    for index, field in enumerate(fields):
        for key in ["name", "type", "role"]:
            if not field.get(key):
                issues.append({"severity": "error", "path": f"schema.fields[{index}].{key}", "message": "field contract key is missing"})
    if "missing_rate" not in quality:
        issues.append({"severity": "warn", "path": "quality.missing_rate", "message": "missing-rate statistics are unavailable"})
    if dataset.get("records_count", 0) and not preview:
        issues.append({"severity": "warn", "path": "records_preview", "message": "records exist but preview is empty"})

    severity_rank = {"error": 2, "warn": 1}
    status = "ok"
    if any(item["severity"] == "error" for item in issues):
        status = "fail"
    elif any(item["severity"] == "warn" for item in issues):
        status = "warn"

    role_counts = Counter(field.get("role", "unknown") for field in fields)
    type_counts = Counter(field.get("type", "unknown") for field in fields)
    return {
        "status": status,
        "issues": sorted(issues, key=lambda item: severity_rank.get(item["severity"], 0), reverse=True),
        "availability": {
            "has_records": dataset.get("records_count", 0) > 0,
            "has_schema": bool(fields),
            "has_metrics": any(field.get("role") == "metric" for field in fields),
            "has_dimensions": any(field.get("role") == "dimension" for field in fields),
            "has_labels": any(field.get("role") == "label" for field in fields),
            "has_preview": bool(preview),
            "chart_count": len(payload.get("suggested_charts") or []),
        },
        "role_counts": dict(role_counts),
        "type_counts": dict(type_counts),
    }


def _coerce_record(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    return {"value": item}


def _unwrap_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    if isinstance(analysis.get("data"), dict):
        return {**analysis, **analysis["data"]}
    return analysis


def _dataset_name_from_analysis(analysis: dict[str, Any]) -> str:
    goal = str(analysis.get("goal") or "").strip()
    if goal:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", goal.lower()).strip("_")[:80] or "site_analysis"
    url = str(analysis.get("url") or "").strip()
    if url:
        host = re.sub(r"^https?://", "", url).split("/", 1)[0]
        return host.replace(".", "_").replace(":", "_") or "site_analysis"
    return ""


def _field_order(records: list[dict[str, Any]], analysis: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for row in records:
        for key in row:
            if key not in seen:
                seen.append(key)
    recommended = analysis.get("recommended_schema") or {}
    for field in recommended.get("fields", []) if isinstance(recommended, dict) else []:
        name = field.get("name") if isinstance(field, dict) else str(field)
        if name and name not in seen:
            seen.append(name)
    field_quality = analysis.get("field_quality") or {}
    for field in field_quality.get("fields", []) if isinstance(field_quality, dict) else []:
        name = field.get("field") if isinstance(field, dict) else ""
        if name and name not in seen:
            seen.append(name)
    return seen


def _field_schema(name: str, values: list[Any]) -> dict[str, Any]:
    non_empty = [_stringify(value) for value in values if not _is_missing(value)]
    sample_values = _dedupe(non_empty)[:5]
    field_type = _infer_type(name, non_empty)
    return {
        "name": name,
        "type": field_type,
        "role": _infer_role(name, field_type, non_empty),
        "missing_rate": _missing_rate(values),
        "unique_count": len(set(non_empty)),
        "sample_values": sample_values,
    }


def _infer_type(name: str, values: list[str]) -> str:
    if URL_FIELD_RE.search(name):
        return "url"
    if DATE_FIELD_RE.search(name) or _looks_like_date_values(values):
        return "date"
    if NUMBER_FIELD_RE.search(name) or _numeric_ratio(values) >= 0.8:
        return "number"
    if values and all(value.lower() in {"true", "false", "0", "1", "yes", "no"} for value in values[:20]):
        return "boolean"
    if LONG_TEXT_FIELD_RE.search(name) or _average_length(values) > 120:
        return "text"
    unique_count = len(set(values))
    if CATEGORY_FIELD_RE.search(name) or (values and unique_count <= max(20, math.ceil(len(values) * 0.35))):
        return "category"
    return "text"


def _infer_role(name: str, field_type: str, values: list[str]) -> str:
    if field_type == "url":
        return "metadata"
    if field_type == "number":
        return "metric"
    if field_type in {"date", "category", "boolean"}:
        return "dimension"
    if LABEL_FIELD_RE.search(name):
        return "label"
    if LONG_TEXT_FIELD_RE.search(name):
        return "metadata"
    return "label" if _average_length(values) <= 80 else "metadata"


def _quality_report(records: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    missing = {field: _missing_rate([row.get(field) for row in records]) for field in fields}
    duplicate_rate = _duplicate_rate(records)
    completeness = 1.0
    if missing:
        completeness = round(1 - (sum(missing.values()) / len(missing)), 4)
    return {
        "records_count": len(records),
        "field_count": len(fields),
        "missing_rate": missing,
        "duplicate_rate": duplicate_rate,
        "completeness_score": completeness,
        "empty_records": sum(1 for row in records if not any(not _is_missing(value) for value in row.values())),
    }


def _suggest_charts(fields: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    dimensions = [field for field in fields if field.get("role") == "dimension"]
    metrics = [field for field in fields if field.get("role") == "metric"]
    labels = [field for field in fields if field.get("role") == "label"]
    date_fields = [field for field in dimensions if field.get("type") == "date"]
    category_fields = [field for field in dimensions if field.get("type") in {"category", "boolean"}]

    if category_fields:
        field = category_fields[0]["name"]
        charts.append({"type": "bar", "title": f"Records by {field}", "x": field, "y": "count", "confidence": 0.82})
    if date_fields:
        field = date_fields[0]["name"]
        charts.append({"type": "line", "title": f"Records over {field}", "x": field, "y": "count", "confidence": 0.78})
    if metrics:
        field = metrics[0]["name"]
        charts.append({"type": "histogram", "title": f"Distribution of {field}", "x": field, "y": "count", "confidence": 0.74})
    if len(metrics) >= 2:
        charts.append({
            "type": "scatter",
            "title": f"{metrics[0]['name']} vs {metrics[1]['name']}",
            "x": metrics[0]["name"],
            "y": metrics[1]["name"],
            "confidence": 0.68,
        })
    if labels or records:
        columns = [field["name"] for field in [*labels[:3], *dimensions[:2], *metrics[:2]]]
        charts.append({"type": "table", "title": "Records Preview", "columns": _dedupe(columns), "confidence": 0.9})
    return charts


def _analysis_context(analysis: dict[str, Any]) -> dict[str, Any]:
    if not analysis:
        return {}
    summary = analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}
    profile = analysis.get("site_profile") if isinstance(analysis.get("site_profile"), dict) else {}
    quality = analysis.get("field_quality") if isinstance(analysis.get("field_quality"), dict) else {}
    return {
        "url": analysis.get("url", ""),
        "goal": analysis.get("goal", ""),
        "best_mode": summary.get("best_mode", ""),
        "site_type": profile.get("site_type", ""),
        "page_type": profile.get("page_type", ""),
        "field_quality_grade": quality.get("overall_grade", ""),
    }


def _handoff_notes(records: list[dict[str, Any]], fields: list[dict[str, Any]], quality: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not records:
        notes.append("No records were provided; payload contains schema and analysis context only.")
    if any(field.get("role") == "metric" for field in fields):
        notes.append("Numeric metrics are available for distribution or comparison charts.")
    if any(field.get("type") == "date" for field in fields):
        notes.append("Date-like fields are available for time-series charts.")
    if quality.get("duplicate_rate", 0) > 0.05:
        notes.append("Duplicate rate is above 5%; consider deduplication before final reporting.")
    if analysis:
        notes.append("Analysis context from Crawpapa-Fetch is included for lineage and crawler strategy review.")
    return notes


def _missing_rate(values: list[Any]) -> float:
    if not values:
        return 0.0
    return round(sum(1 for value in values if _is_missing(value)) / len(values), 4)


def _duplicate_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    serialized = [json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in records]
    unique = len(set(serialized))
    return round((len(serialized) - unique) / len(serialized), 4)


def _numeric_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if _to_number(value) is not None) / len(values)


def _looks_like_date_values(values: list[str]) -> bool:
    if not values:
        return False
    sample = values[:20]
    hits = sum(1 for value in sample if re.search(r"\b\d{4}[-/年.]\d{1,2}([-/月.]\d{1,2})?\b", value))
    return hits / len(sample) >= 0.6


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def _average_length(values: list[str]) -> float:
    if not values:
        return 0.0
    return sum(len(value) for value in values) / len(values)


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or str(value).strip().lower() in {"none", "null", "nan"}


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _dedupe(values: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result
