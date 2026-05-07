"""Job record normalization helpers for collection QA and analysis."""

from __future__ import annotations

import csv
import html as html_lib
import json
import re
import warnings
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning


CITY_ALIASES = {
    "San Jose": ("United States", "California", "San Jose"),
    "San Francisco": ("United States", "California", "San Francisco"),
    "New York": ("United States", "New York", "New York"),
    "Palo Alto": ("United States", "California", "Palo Alto"),
    "Irvine": ("United States", "California", "Irvine"),
    "北京": ("China", "北京", "北京"),
    "上海": ("China", "上海", "上海"),
    "深圳": ("China", "广东", "深圳"),
    "广州": ("China", "广东", "广州"),
    "郑州": ("China", "河南", "郑州"),
    "苏州": ("China", "江苏", "苏州"),
    "南京": ("China", "江苏", "南京"),
    "杭州": ("China", "浙江", "杭州"),
    "成都": ("China", "四川", "成都"),
    "重庆": ("China", "重庆", "重庆"),
    "大连": ("China", "辽宁", "大连"),
}


FOOTER_MARKERS = [
    "相似职位",
    "其它招聘",
    "相关推荐",
    "公司信息",
    "温馨提示",
]


LEGAL_MARKERS = [
    "版权所有",
    "ICP备",
    "公网安备",
    "用户协议",
    "隐私政策",
    "Copyright",
    "All Rights Reserved",
]


def clean_space(text: Any) -> str:
    if text is None:
        return ""
    value = html_lib.unescape(str(text))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def clean_job_description(text: Any, max_length: int = 1200) -> str:
    value = clean_space(text)
    for marker in FOOTER_MARKERS:
        idx = value.find(marker)
        if idx >= 100:
            value = value[:idx]
    for marker in LEGAL_MARKERS:
        idx = value.find(marker)
        if idx >= 0:
            value = value[:idx]
    value = re.sub(r"[\u4e00-\u9fa5]*ICP备[0-9A-Za-z\-号]*", "", value)
    value = re.sub(r"[\u4e00-\u9fa5]*公网安备[0-9A-Za-z\-号]*", "", value)
    return clean_space(value)[:max_length]


def normalize_title(title: Any) -> str:
    value = clean_space(title)
    lowered = value.lower()
    if "agent engineer" in lowered or "ai agent" in lowered:
        return "AI Agent Engineer"
    if "智能体" in value or "Agent开发" in value or "agent开发" in lowered:
        return "AI Agent Engineer"
    if "ai engineer" in lowered and "agent" in lowered:
        return "AI Agent Engineer"
    if "ai engineer" in lowered:
        return "AI Engineer"
    return value


def infer_job_category(title: Any, description: Any = "") -> str:
    blob = f"{clean_space(title)} {clean_space(description)}".lower()
    if "marketing" in blob:
        return "AI Agent - Marketing"
    if "customer success" in blob:
        return "AI Agent - Customer Success"
    if "agent" in blob or "智能体" in blob:
        return "AI Agent Development"
    if "llm" in blob or "大模型" in blob:
        return "LLM Application Development"
    return "AI Engineering"


def normalize_location(raw: Any) -> dict[str, str]:
    value = clean_space(raw)
    lowered = value.lower()
    remote = bool(re.search(r"remote|远程", value, re.I))
    countries: list[str] = []
    provinces: list[str] = []
    cities: list[str] = []
    for alias, mapped in CITY_ALIASES.items():
        if alias.lower() in lowered:
            country, province, city = mapped
            if country not in countries:
                countries.append(country)
            if province not in provinces:
                provinces.append(province)
            if city not in cities:
                cities.append(city)
    if not cities and ("United States" in value or "California" in value or "New York" in value):
        countries.append("United States")
    if "未公开" in value:
        value = ""
    return {
        "location_raw": value,
        "country": "/".join(countries),
        "province_state": "/".join(provinces),
        "city": "/".join(cities),
        "is_remote": remote,
    }


def parse_salary(raw: Any) -> dict[str, Any]:
    value = clean_space(raw)
    result: dict[str, Any] = {
        "salary_raw": value,
        "currency": "",
        "salary_min": None,
        "salary_max": None,
        "salary_period": "",
        "salary_negotiable": False,
        "benefits": "",
    }
    if not value or "未公开" in value:
        return result
    if "面议" in value or "competitive" in value.lower():
        result["salary_negotiable"] = True
    if any(k in value for k in ["福利", "五险一金", "奖金", "期权", "股权", "体检", "年假"]):
        result["benefits"] = value

    usd = re.search(r"USD\s*([0-9,]+)\s*-\s*([0-9,]+)\s*/\s*([A-Z]+)", value, re.I)
    if usd:
        result.update({
            "currency": "USD",
            "salary_min": int(usd.group(1).replace(",", "")),
            "salary_max": int(usd.group(2).replace(",", "")),
            "salary_period": usd.group(3).upper(),
        })
        return result

    usd2 = re.search(r"\$\s*([0-9,]+)\s*(?:k|K)?\s*[-~]\s*\$?\s*([0-9,]+)\s*(?:k|K)?", value)
    if usd2:
        lo = int(usd2.group(1).replace(",", ""))
        hi = int(usd2.group(2).replace(",", ""))
        if lo < 1000:
            lo *= 1000
        if hi < 1000:
            hi *= 1000
        result.update({"currency": "USD", "salary_min": lo, "salary_max": hi, "salary_period": "YEAR"})
        return result

    cny = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[-~]\s*([0-9]+(?:\.[0-9]+)?)\s*[kK]", value)
    if cny:
        result.update({
            "currency": "CNY",
            "salary_min": int(float(cny.group(1)) * 1000),
            "salary_max": int(float(cny.group(2)) * 1000),
            "salary_period": "MONTH",
        })
    return result


def quality_grade(record: dict[str, Any]) -> str:
    status = str(record.get("fetch_status", ""))
    has_salary = bool(record.get("salary_min") and record.get("salary_max")) or bool(record.get("salary_negotiable"))
    has_location = bool(record.get("city") or record.get("country") or record.get("is_remote"))
    has_desc = len(str(record.get("description_clean") or "")) >= 120
    if status == "parsed_jsonld" and has_salary and has_location and has_desc:
        return "A"
    if status.startswith("parsed_") and has_desc and (has_salary or has_location):
        return "B"
    if "fallback" in status:
        return "C"
    return "D"


def normalize_job_record(record: dict[str, Any], fetch_time: str = "") -> dict[str, Any]:
    title = record.get("title") or record.get("title_raw") or ""
    location = record.get("location") or record.get("location_raw") or ""
    salary = record.get("salary_or_benefits") or record.get("salary_raw") or ""
    description = record.get("description_requirements") or record.get("description") or record.get("description_clean") or ""
    description_clean = clean_job_description(description)
    normalized = {
        "title_raw": clean_space(title),
        "title_normalized": normalize_title(title),
        "job_category": infer_job_category(title, description_clean),
        **normalize_location(location),
        **parse_salary(salary),
        "description_clean": description_clean,
        "source_channel": clean_space(record.get("source_channel") or record.get("source") or ""),
        "url": clean_space(record.get("url") or ""),
        "fetch_status": clean_space(record.get("fetch_status") or ""),
        "publish_date": clean_space(record.get("publish_date") or record.get("datePosted") or ""),
        "fetch_time": fetch_time or datetime.now(timezone.utc).isoformat(),
    }
    normalized["quality_grade"] = quality_grade(normalized)
    return normalized


def _records_from_csv_text(text: str) -> list[dict[str, Any]]:
    return list(csv.DictReader(StringIO(text)))


def load_records(records: str = "", input_path: str = "", input_format: str = "auto") -> list[dict[str, Any]]:
    raw = records
    if input_path:
        raw = Path(input_path).read_text(encoding="utf-8-sig")
    if not raw:
        return []
    fmt = (input_format or "auto").lower()
    if fmt == "auto":
        stripped = raw.lstrip()
        fmt = "json" if stripped.startswith("[") or stripped.startswith("{") else "csv"
    if fmt == "csv":
        return _records_from_csv_text(raw)
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return parsed["records"]
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("records must be a CSV string, JSON object, JSON array, or {records: [...]}")


def normalize_job_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    fetch_time = datetime.now(timezone.utc).isoformat()
    normalized = [normalize_job_record(record, fetch_time=fetch_time) for record in records]
    grade_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    salary_structured_count = 0
    location_structured_count = 0
    for record in normalized:
        grade = record["quality_grade"]
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
        source = record.get("source_channel") or ""
        source_counts[source] = source_counts.get(source, 0) + 1
        if record.get("salary_min") and record.get("salary_max"):
            salary_structured_count += 1
        if record.get("city") or record.get("country") or record.get("is_remote"):
            location_structured_count += 1
    return {
        "records": normalized,
        "summary": {
            "record_count": len(normalized),
            "grade_counts": grade_counts,
            "source_counts": source_counts,
            "salary_structured_count": salary_structured_count,
            "location_structured_count": location_structured_count,
        },
    }
