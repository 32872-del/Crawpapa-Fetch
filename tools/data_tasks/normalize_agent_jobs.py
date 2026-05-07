import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output"
SRC = OUT / "agent_developer_jobs.csv"
NORMALIZED = OUT / "agent_developer_jobs_normalized.csv"
REPORT = OUT / "agent_developer_jobs_quality_report.json"


CITY_ALIASES = {
    "San Jose": ("United States", "California", "San Jose"),
    "San Francisco": ("United States", "California", "San Francisco"),
    "New York": ("United States", "New York", "New York"),
    "北京": ("China", "北京", "北京"),
    "上海": ("China", "上海", "上海"),
    "深圳": ("China", "广东", "深圳"),
    "广州": ("China", "广东", "广州"),
    "郑州": ("China", "河南", "郑州"),
    "苏州": ("China", "江苏", "苏州"),
    "南京": ("China", "江苏", "南京"),
    "成都": ("China", "四川", "成都"),
    "大连": ("China", "辽宁", "大连"),
}


FOOTER_MARKERS = [
    "相似职位",
    "公司信息",
    "温馨提示",
    "版权所有",
    "ICP备",
    "公网安备",
    "用户协议",
    "隐私政策",
    "其它招聘",
    "Copyright",
    "All Rights Reserved",
]


def clean_space(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_title(title):
    text = clean_space(title)
    lower = text.lower()
    if "agent engineer" in lower or "ai agent" in lower:
        return "AI Agent Engineer"
    if "智能体" in text or "Agent开发" in text or "agent开发" in lower:
        return "AI Agent Engineer"
    if "ai engineer" in lower and "agent" in lower:
        return "AI Agent Engineer"
    if "ai engineer" in lower:
        return "AI Engineer"
    return text


def job_category(title, desc):
    blob = f"{title} {desc}".lower()
    if "marketing" in blob:
        return "AI Agent - Marketing"
    if "customer success" in blob:
        return "AI Agent - Customer Success"
    if "agent" in blob or "智能体" in blob:
        return "AI Agent Development"
    if "llm" in blob or "大模型" in blob:
        return "LLM Application Development"
    return "AI Engineering"


def normalize_location(raw):
    raw = clean_space(raw)
    remote = bool(re.search(r"remote|远程", raw, re.I))
    countries, provinces, cities = [], [], []
    for alias, mapped in CITY_ALIASES.items():
        if alias.lower() in raw.lower():
            country, province, city = mapped
            if country not in countries:
                countries.append(country)
            if province not in provinces:
                provinces.append(province)
            if city not in cities:
                cities.append(city)
    if not cities and ("United States" in raw or "California" in raw or "New York" in raw):
        if "United States" not in countries:
            countries.append("United States")
    if not cities and ("未公开" in raw or not raw):
        raw = ""
    return {
        "location_raw": raw,
        "country": "/".join(countries),
        "province_state": "/".join(provinces),
        "city": "/".join(cities),
        "is_remote": str(remote).lower(),
    }


def parse_salary(raw):
    text = clean_space(raw)
    result = {
        "salary_raw": text,
        "currency": "",
        "salary_min": "",
        "salary_max": "",
        "salary_period": "",
        "salary_negotiable": "false",
        "benefits": "",
    }
    if not text or "未公开" in text:
        return result
    if "面议" in text or "competitive" in text.lower():
        result["salary_negotiable"] = "true"
    if any(k in text for k in ["福利", "五险一金", "奖金", "期权", "股权", "体检", "年假"]):
        result["benefits"] = text

    usd = re.search(r"USD\s*([0-9,]+)\s*-\s*([0-9,]+)\s*/\s*([A-Z]+)", text, re.I)
    if usd:
        result.update(
            {
                "currency": "USD",
                "salary_min": usd.group(1).replace(",", ""),
                "salary_max": usd.group(2).replace(",", ""),
                "salary_period": usd.group(3).upper(),
            }
        )
        return result

    usd2 = re.search(r"\$\s*([0-9,]+)\s*(?:k|K)?\s*[-~]\s*\$?\s*([0-9,]+)\s*(?:k|K)?", text)
    if usd2:
        lo = int(usd2.group(1).replace(",", ""))
        hi = int(usd2.group(2).replace(",", ""))
        if lo < 1000:
            lo *= 1000
        if hi < 1000:
            hi *= 1000
        result.update({"currency": "USD", "salary_min": str(lo), "salary_max": str(hi), "salary_period": "YEAR"})
        return result

    cny = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[-~]\s*([0-9]+(?:\.[0-9]+)?)\s*[kK]", text)
    if cny:
        result.update(
            {
                "currency": "CNY",
                "salary_min": str(int(float(cny.group(1)) * 1000)),
                "salary_max": str(int(float(cny.group(2)) * 1000)),
                "salary_period": "MONTH",
            }
        )
    return result


def clean_description(text):
    text = clean_space(text)
    for marker in FOOTER_MARKERS:
        idx = text.find(marker)
        if idx >= 0 and idx > 100:
            text = text[:idx]
    text = re.sub(r"沪ICP备[0-9A-Za-z\-号]+", "", text)
    text = re.sub(r"[\u4e00-\u9fa5]*公网安备[0-9A-Za-z\-号]+", "", text)
    return clean_space(text)[:1200]


def quality_grade(row):
    status = row.get("fetch_status", "")
    has_salary = bool(row.get("salary_min") and row.get("salary_max")) or row.get("salary_negotiable") == "true"
    has_location = bool(row.get("city") or row.get("country") or row.get("is_remote") == "true")
    has_desc = len(row.get("description_clean", "")) >= 120
    if status == "parsed_jsonld" and has_salary and has_location and has_desc:
        return "A"
    if status.startswith("parsed_") and has_desc and (has_salary or has_location):
        return "B"
    if "fallback" in status:
        return "C"
    return "D"


def main():
    rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))
    normalized = []
    fetch_time = datetime.now(timezone.utc).isoformat()
    for row in rows:
        desc = clean_description(row.get("description_requirements", ""))
        loc = normalize_location(row.get("location", ""))
        salary = parse_salary(row.get("salary_or_benefits", ""))
        out = {
            "title_raw": row.get("title", ""),
            "title_normalized": normalize_title(row.get("title", "")),
            "job_category": job_category(row.get("title", ""), desc),
            **loc,
            **salary,
            "description_clean": desc,
            "source_channel": row.get("source_channel", ""),
            "url": row.get("url", ""),
            "fetch_status": row.get("fetch_status", ""),
            "fetch_time": fetch_time,
        }
        out["quality_grade"] = quality_grade(out)
        normalized.append(out)

    fields = [
        "title_raw",
        "title_normalized",
        "job_category",
        "location_raw",
        "country",
        "province_state",
        "city",
        "is_remote",
        "salary_raw",
        "currency",
        "salary_min",
        "salary_max",
        "salary_period",
        "salary_negotiable",
        "benefits",
        "description_clean",
        "source_channel",
        "url",
        "fetch_status",
        "quality_grade",
        "fetch_time",
    ]
    with NORMALIZED.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(normalized)

    grade_counts = {}
    source_counts = {}
    salary_structured = 0
    location_structured = 0
    for row in normalized:
        grade_counts[row["quality_grade"]] = grade_counts.get(row["quality_grade"], 0) + 1
        source_counts[row["source_channel"]] = source_counts.get(row["source_channel"], 0) + 1
        if row["salary_min"] and row["salary_max"]:
            salary_structured += 1
        if row["city"] or row["country"] or row["is_remote"] == "true":
            location_structured += 1

    REPORT.write_text(
        json.dumps(
            {
                "input_csv": str(SRC),
                "normalized_csv": str(NORMALIZED),
                "record_count": len(normalized),
                "grade_counts": grade_counts,
                "source_counts": source_counts,
                "salary_structured_count": salary_structured,
                "location_structured_count": location_structured,
                "schema_added": fields,
                "remaining_gaps": [
                    "publish_date is unavailable for most current rows and should be extracted at source level.",
                    "fallback rows should be manually verified before decision-grade analysis.",
                    "sample size remains too small for market-level conclusions.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"normalized_csv": str(NORMALIZED), "report": str(REPORT), "records": len(normalized)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
