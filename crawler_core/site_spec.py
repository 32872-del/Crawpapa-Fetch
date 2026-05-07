"""Helpers for generating and validating fnspider site specs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


SAFE_SITE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")


def safe_site_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())[:80].strip("._-")
    if not name:
        name = "generated_site"
    if not SAFE_SITE_NAME_RE.match(name):
        raise ValueError("site name contains unsafe characters")
    return name


def draft_site_spec(
    goal: str,
    start_url: str,
    list_selector: str,
    fields: str | dict[str, Any],
    site: str = "",
    mode: str = "auto",
    pagination: str | dict[str, Any] = "",
    variants: str | dict[str, Any] = "",
    wait_selector: str = "",
    render_time: float = 3.0,
    scroll_count: int = 0,
    scroll_delay: float = 1.0,
) -> dict[str, Any]:
    field_spec = json.loads(fields) if isinstance(fields, str) and fields.strip() else fields
    if not isinstance(field_spec, dict) or not field_spec:
        raise ValueError("fields must be a non-empty JSON object")

    parsed_pagination = _json_obj(pagination)
    parsed_variants = _json_obj(variants)
    site_name = safe_site_name(site or _site_from_goal(goal, start_url))
    spec = {
        "version": "1.0",
        "site": site_name,
        "goal": goal,
        "mode": mode or "auto",
        "start_urls": [{"url": start_url}],
        "pagination": parsed_pagination,
        "list": {"item_link": list_selector},
        "detail": field_spec,
        "variants": parsed_variants,
        "wait_selector": wait_selector,
        "sleep_time": render_time,
        "scroll_count": max(0, int(scroll_count or 0)),
        "scroll_delay": float(scroll_delay or 1.0),
        "dedupe": ["categories_1", "categories_2", "categories_3", "url"],
        "required_fields": ["handle", "title", "image_src", "price"],
    }
    return spec


def validate_spec_shape(spec: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    if not spec.get("site"):
        issues.append("site is required")
    if not isinstance(spec.get("start_urls"), list) or not spec.get("start_urls"):
        issues.append("start_urls must be a non-empty list")
    if not isinstance(spec.get("list"), dict) or not spec.get("list", {}).get("item_link"):
        issues.append("list.item_link is required")
    if not isinstance(spec.get("detail"), dict) or not spec.get("detail"):
        issues.append("detail must be a non-empty object")
    return issues


def validate_spec_against_html(spec: dict[str, Any], list_html: str, detail_html: str = "") -> dict[str, Any]:
    issues = validate_spec_shape(spec)
    if issues:
        return {"ok": False, "issues": issues, "summary": {}, "samples": {}}

    start_url = _first_start_url(spec)
    list_soup = BeautifulSoup(list_html or "", "html.parser")
    links = _select_values(list_soup, spec["list"]["item_link"], start_url)
    samples: dict[str, Any] = {"links": links[:5]}
    summary: dict[str, Any] = {
        "list_links_found": len(links),
        "field_hits": {},
        "field_values": {},
        "score": 0,
    }
    if not links:
        issues.append("list.item_link matched 0 links")

    if detail_html:
        detail_url = links[0] if links else start_url
        detail_soup = BeautifulSoup(detail_html, "html.parser")
        for field, rule in spec.get("detail", {}).items():
            values = _select_any(detail_soup, rule, detail_url)
            summary["field_hits"][field] = len(values)
            summary["field_values"][field] = values[:3]
            if not values:
                issues.append(f"detail.{field} matched 0 values")
        samples["record"] = {
            field: values[0] if values else ""
            for field, values in summary["field_values"].items()
        }

    checks = 1 + len(spec.get("detail", {}))
    passed = (1 if links else 0) + sum(1 for count in summary["field_hits"].values() if count)
    summary["score"] = round(passed / checks, 3) if checks else 0
    return {"ok": not issues, "issues": issues, "summary": summary, "samples": samples}


def write_spider_package(spec: dict[str, Any], spider_root: str | Path) -> dict[str, str]:
    issues = validate_spec_shape(spec)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(spider_root)
    if not root.exists():
        raise FileNotFoundError(f"spider root does not exist: {root}")
    site = safe_site_name(str(spec["site"]))
    specs_dir = root / "site_specs"
    versions_dir = specs_dir / "_versions" / site
    specs_dir.mkdir(exist_ok=True)
    versions_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / f"{site}.json"
    runner_path = root / f"run_{site}.py"
    version_path = versions_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    version_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    runner_path.write_text(_runner_source(spec_path.name), encoding="utf-8")
    return {"spec_path": str(spec_path), "runner_path": str(runner_path), "version_path": str(version_path)}


def list_spec_versions(spider_root: str | Path, site: str) -> list[dict[str, Any]]:
    root = Path(spider_root)
    site_name = safe_site_name(site)
    versions_dir = root / "site_specs" / "_versions" / site_name
    result = []
    for path in sorted(versions_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        result.append({
            "version": path.stem,
            "path": str(path),
            "site": data.get("site", site_name),
            "goal": data.get("goal", ""),
            "mode": data.get("mode", ""),
            "fields": sorted((data.get("detail") or {}).keys()),
        })
    return result


def rollback_spec_version(spider_root: str | Path, site: str, version: str = "") -> dict[str, str]:
    root = Path(spider_root)
    site_name = safe_site_name(site)
    versions = list_spec_versions(root, site_name)
    if not versions:
        raise FileNotFoundError(f"no versions found for {site_name}")
    selected = next((item for item in versions if item["version"] == version), versions[0] if not version else None)
    if not selected:
        raise FileNotFoundError(f"version not found: {version}")
    source = Path(selected["path"])
    target = root / "site_specs" / f"{site_name}.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return {"spec_path": str(target), "restored_from": str(source), "version": selected["version"]}


def _runner_source(spec_filename: str) -> str:
    return (
        "from pathlib import Path\n"
        "from fnspider.ConfigSpider import ConfigSpider\n\n"
        "SPEC_PATH = Path(__file__).parent / 'site_specs' / "
        f"{spec_filename!r}\n\n"
        "if __name__ == '__main__':\n"
        "    ConfigSpider(spec_path=str(SPEC_PATH)).start()\n"
    )


def _select_any(soup: BeautifulSoup, rule: Any, base_url: str) -> list[str]:
    if isinstance(rule, list):
        values: list[str] = []
        for item in rule:
            values.extend(_select_values(soup, str(item), base_url))
        return _dedupe(values)
    return _select_values(soup, str(rule), base_url)


def _select_values(soup: BeautifulSoup, expression: str, base_url: str) -> list[str]:
    selector, attr = _selector_parts(expression)
    if not selector:
        return []
    values = []
    for element in soup.select(selector):
        if attr == "text":
            value = element.get_text(" ", strip=True)
        elif attr == "html":
            value = "".join(str(child) for child in element.contents).strip()
        else:
            value = element.get(attr, "")
        if not value:
            continue
        if attr in {"href", "src", "srcset"}:
            value = urljoin(base_url, value)
        values.append(value)
    return _dedupe(values)


def _selector_parts(expression: str) -> tuple[str, str]:
    if "@" not in expression:
        return expression.strip(), "text"
    selector, attr = expression.rsplit("@", 1)
    return selector.strip(), attr.strip() or "text"


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _json_obj(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("value must be a JSON object")
    return parsed


def _first_start_url(spec: dict[str, Any]) -> str:
    first = spec.get("start_urls", [{}])[0]
    return first if isinstance(first, str) else str(first.get("url", ""))


def _site_from_goal(goal: str, start_url: str) -> str:
    host = re.sub(r"^https?://", "", start_url).split("/", 1)[0]
    return host.replace(":", "_") or goal[:40]
