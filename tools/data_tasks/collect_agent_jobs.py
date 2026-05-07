import csv
import html as html_lib
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import unified_crawler_server as mcp  # noqa: E402


OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)


URLS = [
    ("Ashby", "https://jobs.ashbyhq.com/tessera-labs/0cb577c2-6cdd-4361-b1af-870ccfc9d792/"),
    ("Ashby", "https://jobs.ashbyhq.com/Varick-Agents/30e16a2a-6374-475d-9154-2e186c481319/"),
    ("Ashby", "https://jobs.ashbyhq.com/fieldguide/6d0ceade-cbbe-4cd7-9af9-caf3cfed7ec3/"),
    ("Ashby", "https://jobs.ashbyhq.com/wispr-flow/3d1542ef-73da-48e2-af42-6379c6d967e9"),
    ("Ashby", "https://jobs.ashbyhq.com/lumos/b1e3ef63-78da-41a2-a41e-2e01f943fb30/"),
    ("Ashby", "https://jobs.ashbyhq.com/vapi/7adb10b2-1234-4e4c-b132-f990a91de048/"),
    ("Lever", "https://jobs.lever.co/jobgether/0e2414ec-a800-4489-b52d-e0b5d9760ed3"),
    ("\u725b\u5ba2\u7f51", "https://www.nowcoder.com/jobs/detail/422387?urlSource=sitemap"),
    ("\u5706\u624d\u7f51", "https://www.o-hr.com/recruit/job/detail/Ii8Afh5r"),
    ("\u8fdc\u7a0b\u5de5\u4f5c\u8005", "https://remote-china.com/jobs/735"),
    ("\u804c\u5750\u6807", "https://job.zhizuobiao.com/gwai000222.html"),
    ("\u9ad8\u6821\u4eba\u624d\u7f51", "https://www.gaoxiaojob.com/bk_jobs/grrc5hhl"),
    ("\u5ce8\u7709\u5c71\u4eba\u624d\u7f51", "https://m.emeishan.sclsrcw.com/job/308278193.html"),
    ("DTNS\u5b98\u7f51", "https://www.dtns.top/join.html"),
]


BLOCKED_PLATFORMS = [
    {
        "source_channel": "BOSS\u76f4\u8058",
        "status": "skipped",
        "reason": "robots.txt disallow observed in MCP probe",
    },
    {
        "source_channel": "\u62c9\u52fe",
        "status": "skipped",
        "reason": "challenge/captcha observed in MCP probe",
    },
    {
        "source_channel": "\u667a\u8054\u62db\u8058",
        "status": "skipped",
        "reason": "challenge/empty shell observed in MCP probe",
    },
]


CN_KEYWORDS = [
    "\u667a\u80fd\u4f53",
    "Agent\u5f00\u53d1",
    "\u591a\u667a\u80fd\u4f53",
    "\u5927\u6a21\u578b",
]
EN_KEYWORDS = ["ai agent", "agent", "llm"]


def fetch_best(url):
    candidates = []
    for mode in ["curl_cffi", "requests", "auto"]:
        html = mcp.fetch_page(url, mode=mode, use_cache=False, respect_robots=True, allow_private=False)
        score = len(html or "")
        if isinstance(html, str):
            if "JobPosting" in html:
                score += 100000
            if "application/ld+json" in html:
                score += 50000
            if html.startswith("{") and ("fetch_failed" in html or '"error"' in html):
                score -= 100000
            if "captcha" in html.lower() or "\u9a8c\u8bc1\u7801" in html:
                score -= 50000
        candidates.append((score, mode, html))
        if score > 100000:
            break
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, mode, html = candidates[0]
    return html, mode, score


def clean_text(value, limit=None):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(i) for i in value if i)
    text = html_lib.unescape(str(value))
    text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def is_agent_related(text):
    lowered = text.lower()
    return any(k in lowered for k in EN_KEYWORDS) or any(k in text for k in CN_KEYWORDS)


def flatten_jsonld(data):
    if isinstance(data, list):
        out = []
        for item in data:
            out.extend(flatten_jsonld(item))
        return out
    if isinstance(data, dict):
        out = []
        if "@graph" in data:
            out.extend(flatten_jsonld(data.get("@graph")))
        out.append(data)
        return out
    return []


def parse_jsonld(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for item in flatten_jsonld(data):
            typ = item.get("@type") or item.get("type")
            if isinstance(typ, list):
                is_job = any(str(t).lower() == "jobposting" for t in typ)
            else:
                is_job = str(typ).lower() == "jobposting"
            if is_job:
                jobs.append(item)
    return jobs


def location_from_job(job):
    loc = job.get("jobLocation") or job.get("applicantLocationRequirements") or ""

    def one(item):
        if isinstance(item, dict):
            addr = item.get("address") or item
            if isinstance(addr, dict):
                bits = [
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("addressCountry", ""),
                    addr.get("streetAddress", ""),
                ]
                return ", ".join(clean_text(b) for b in bits if b)
            return clean_text(addr)
        return clean_text(item)

    vals = [one(i) for i in loc] if isinstance(loc, list) else ([one(loc)] if loc else [])
    vals = [v for v in vals if v]
    if job.get("jobLocationType") == "TELECOMMUTE" and not any("Remote" in v or "\u8fdc\u7a0b" in v for v in vals):
        vals.append("Remote")
    return " / ".join(dict.fromkeys(vals))


def salary_from_text(text):
    text = clean_text(text)
    hits = []
    patterns = [
        r"\$\s?[0-9,]+(?:\.[0-9]+)?\s?(?:K|k)?(?:\s*[-~]\s*\$?\s?[0-9,]+(?:\.[0-9]+)?\s?(?:K|k)?)?(?:\s*/\s*(?:year|yr))?",
        r"[0-9]+(?:\.[0-9]+)?\s*[-~]\s*[0-9]+(?:\.[0-9]+)?\s*(?:k|K)(?:\s*[xX]\s*[0-9]+)?",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, re.I):
            item = clean_text(match)
            if item and item not in hits:
                hits.append(item)

    markers = [
        "\u85aa\u8d44",
        "\u85aa\u916c",
        "\u5f85\u9047",
        "\u798f\u5229",
        "\u4e94\u9669\u4e00\u91d1",
        "\u5e74\u7ec8\u5956",
        "\u5e26\u85aa\u5e74\u5047",
        "\u5b9a\u671f\u4f53\u68c0",
        "equity",
        "stock options",
        "competitive salary",
        "competitive compensation",
    ]
    lower = text.lower()
    for marker in markers:
        idx = lower.find(marker.lower())
        if idx >= 0:
            seg = clean_text(text[max(0, idx - 40) : idx + 180])
            if seg and seg not in hits:
                hits.append(seg)
    return "; ".join(hits[:4]) if hits else "\u672a\u516c\u5f00/\u672a\u63d0\u53d6\u5230"


def salary_from_job(job, text=""):
    base = job.get("baseSalary") if isinstance(job, dict) else None
    if isinstance(base, dict):
        currency = base.get("currency", "")
        value = base.get("value")
        if isinstance(value, dict):
            min_value = value.get("minValue")
            max_value = value.get("maxValue")
            unit = value.get("unitText", "")
            if min_value and max_value:
                return f"{currency} {min_value}-{max_value} / {unit}".strip()
            if value.get("value"):
                return f"{currency} {value.get('value')} / {unit}".strip()
        elif value:
            return f"{currency} {value}".strip()
    return salary_from_text(text)


def title_from_soup(soup):
    for selector in ["h1", ".job-title", "[class*=job-title]", "[class*=JobTitle]", "title"]:
        el = soup.select_one(selector)
        if el:
            title = clean_text(el.get_text(" ", strip=True) if el.name != "title" else el.string)
            if title:
                return title
    meta = soup.find("meta", property="og:title")
    return clean_text(meta.get("content")) if meta else ""


def extract_location_from_text(text):
    markers = [
        "\u5de5\u4f5c\u5730\u70b9",
        "\u5730\u70b9",
        "Location",
        "\u5de5\u4f5c\u57ce\u5e02",
    ]
    for marker in markers:
        idx = text.lower().find(marker.lower())
        if idx >= 0:
            return clean_text(text[idx : idx + 90], 100)
    for city in [
        "\u5317\u4eac",
        "\u4e0a\u6d77",
        "\u6df1\u5733",
        "\u5e7f\u5dde",
        "\u676d\u5dde",
        "\u5357\u4eac",
        "\u82cf\u5dde",
        "\u6210\u90fd",
        "\u91cd\u5e86",
        "\u5927\u8fde",
        "\u8fdc\u7a0b",
        "Remote",
        "San Francisco",
        "San Jose",
        "New York",
        "Palo Alto",
        "Irvine",
    ]:
        if city.lower() in text.lower():
            return city
    return ""


def extract_generic(url, channel, html):
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "noscript", "svg"]):
        bad.decompose()
    text = clean_text(soup.get_text(" ", strip=True))
    title = title_from_soup(soup)
    if not is_agent_related(title + " " + text[:3000]):
        return None

    desc = text
    for key in [
        "\u5c97\u4f4d\u804c\u8d23",
        "\u804c\u4f4d\u63cf\u8ff0",
        "\u5de5\u4f5c\u63cf\u8ff0",
        "Job Summary",
        "\u804c\u4f4d\u8981\u6c42",
        "Requirements",
    ]:
        idx = text.lower().find(key.lower())
        if idx >= 0:
            desc = text[idx : idx + 1400]
            break
    return {
        "title": clean_text(title, 180),
        "location": extract_location_from_text(text) or "\u672a\u516c\u5f00/\u672a\u63d0\u53d6\u5230",
        "salary_or_benefits": clean_text(salary_from_text(text), 220),
        "source_channel": channel,
        "description_requirements": clean_text(desc, 1500),
        "url": url,
        "fetch_status": "parsed_text",
    }


def main():
    rows = []
    fetch_log = []
    seen = set()
    for channel, url in URLS:
        print(f"FETCH {channel} {url}")
        html, fetch_mode, fetch_score = fetch_best(url)
        status = "ok"
        if isinstance(html, str) and html.startswith("{"):
            try:
                parsed = json.loads(html)
                if parsed.get("error") or parsed.get("success") is False:
                    status = "error_json:" + str(parsed.get("error") or parsed.get("message") or parsed.get("code"))[:120]
            except Exception:
                pass
        if not html or len(html) < 200 or status.startswith("error"):
            fetch_log.append({"url": url, "source_channel": channel, "status": status, "mode": fetch_mode, "score": fetch_score, "length": len(html or "")})
            continue

        made = 0
        for job in parse_jsonld(html):
            title = clean_text(job.get("title"), 180)
            desc = clean_text(job.get("description"), 1500)
            if not is_agent_related(title + " " + desc):
                continue
            key = (title, url)
            if key in seen:
                continue
            rows.append(
                {
                    "title": title,
                    "location": location_from_job(job) or "\u672a\u516c\u5f00/\u672a\u63d0\u53d6\u5230",
                    "salary_or_benefits": salary_from_job(job, desc),
                    "source_channel": channel,
                    "description_requirements": desc,
                    "url": url,
                    "fetch_status": "parsed_jsonld",
                }
            )
            seen.add(key)
            made += 1

        if made == 0:
            row = extract_generic(url, channel, html)
            if row and (row["title"], row["url"]) not in seen:
                rows.append(row)
                seen.add((row["title"], row["url"]))
                made = 1
        fetch_log.append({"url": url, "source_channel": channel, "status": status, "mode": fetch_mode, "score": fetch_score, "length": len(html), "records": made})

    supplemental = [
        {
            "title": "\u5927\u6a21\u578b\u667a\u80fd\u4f53\u5f00\u53d1\u5de5\u7a0b\u5e08\uff08\u5357\u4eac\uff09",
            "location": "\u5357\u4eac",
            "salary_or_benefits": "\u85aa\u8d44\u9762\u8bae",
            "source_channel": "\u725b\u5ba2\u7f51",
            "description_requirements": "\u667a\u80fd\u4f53\u8bbe\u8ba1\u4e0e\u5f00\u53d1\uff1a\u57fa\u4e8e\u5927\u8bed\u8a00\u6a21\u578b\u5e94\u7528\u5f00\u53d1\u6846\u67b6\u5f00\u53d1\u9ad8\u6027\u80fd\u667a\u80fd\u4f53\uff0c\u652f\u6301\u5bf9\u8bdd\u751f\u6210\u3001\u4fe1\u606f\u68c0\u7d22\u3001\u4ee3\u7801\u751f\u6210\u7b49\uff1b\u4f18\u5316\u5927\u6a21\u578b\u63a8\u7406\u6027\u80fd\uff1b\u6784\u5efa\u79c1\u57df\u77e5\u8bc6\u548c\u8bc4\u4f30\u6570\u636e\u96c6\uff1b\u8ddf\u8e2a\u5927\u6a21\u578b\u7814\u7a76\u8fdb\u5c55\u5e76\u4e0e\u4ea7\u54c1\u7814\u53d1\u56e2\u961f\u5408\u4f5c\u3002",
            "url": "https://www.nowcoder.com/jobs/detail/422387?urlSource=sitemap",
            "fetch_status": "public_search_result_fallback",
        },
        {
            "title": "Agent\u5f00\u53d1\u5de5\u7a0b\u5e08",
            "location": "\u82cf\u5dde\u5de5\u4e1a\u56ed\u533a",
            "salary_or_benefits": "20~25K\uff1b\u6709\u7ade\u4e89\u529b\u548c\u7075\u6d3b\u7684\u85aa\u916c\u798f\u5229\u4f53\u7cfb",
            "source_channel": "\u5706\u624d\u7f51",
            "description_requirements": "\u672c\u79d1\u53ca\u4ee5\u4e0a\uff0c\u8ba1\u7b97\u673a/\u4eba\u5de5\u667a\u80fd/\u8f6f\u4ef6\u5de5\u7a0b\u76f8\u5173\u4e13\u4e1a\uff1b3\u5e74\u4ee5\u4e0a\u76f8\u5173\u7ecf\u9a8c\uff0c\u719f\u6089\u5927\u8bed\u8a00\u6a21\u578b\u7b97\u6cd5\u5f00\u53d1\u6216AI\u5e94\u7528\uff1b\u638c\u63e1 Java \u5168\u6808\u3001Spring Boot/Spring Cloud/MyBatis\uff0c\u638c\u63e1 TypeScript/JavaScript\u3001React/Vue\uff0c\u80fd\u4f7f\u7528 Prompt\u3001RAG\u3001Agent \u7b49AI\u5e94\u7528\u6280\u672f\u6808\u3002",
            "url": "https://www.o-hr.com/recruit/job/detail/Ii8Afh5r",
            "fetch_status": "public_search_result_fallback",
        },
        {
            "title": "AI Agent \u5de5\u7a0b\u5e08",
            "location": "\u8fdc\u7a0b",
            "salary_or_benefits": "20-40k\uff1b\u85aa\u8d44+\u5956\u91d1+\u671f\u6743/\u80a1\u7968\uff0c\u798f\u5229\u4f53\u7cfb\u4e0e\u804c\u4e1a\u53d1\u5c55\u652f\u6301",
            "source_channel": "\u8fdc\u7a0b\u5de5\u4f5c\u8005",
            "description_requirements": "\u4e3b\u5bfc\u6216\u53c2\u4e0e\u5927\u89c4\u6a21\u3001\u9ad8\u53ef\u7528 AI Agent \u7cfb\u7edf\u67b6\u6784\u8bbe\u8ba1\uff0c\u6db5\u76d6\u4efb\u52a1\u89c4\u5212\u3001\u5de5\u5177\u4f7f\u7528\u3001\u8bb0\u5fc6\u673a\u5236\u3001\u591a\u667a\u80fd\u4f53\u534f\u4f5c\uff1b\u7814\u7a76\u5f3a\u5316\u5b66\u4e60\u3001\u6a21\u4eff\u5b66\u4e60\u3001\u8bfe\u7a0b\u5b66\u4e60\u3001\u56e0\u679c\u63a8\u7406\u3001\u89c4\u5212\u7b49\u6280\u672f\uff1b\u8981\u6c42 Python\u3001PyTorch/TensorFlow\u3001LangChain \u7b49\u7ecf\u9a8c\u3002",
            "url": "https://remote-china.com/jobs/735",
            "fetch_status": "public_search_result_fallback",
        },
        {
            "title": "AI\u667a\u80fd\u4f53\u5f00\u53d1\u5de5\u7a0b\u5e08",
            "location": "\u5927\u8fde",
            "salary_or_benefits": "10-15K\uff1b\u4e94\u9669\u4e00\u91d1\u3001\u7ee9\u6548\u5956\u91d1\u3001\u5e26\u85aa\u5e74\u5047\u3001\u4ea4\u901a\u8865\u52a9\u3001\u5e74\u5ea6\u65c5\u6e38\u3001\u8282\u65e5\u793c\u7269\u3001\u56e2\u961f\u805a\u9910\u3001\u5b9a\u671f\u4f53\u68c0\u3001\u5e74\u7ec8\u5956",
            "source_channel": "\u804c\u5750\u6807",
            "description_requirements": "\u8d1f\u8d23\u5927\u6a21\u578b\u9009\u53d6\u3001\u5fae\u8c03\u3001RAG\u5b9e\u65bd\u8c03\u4f18\u548c\u903b\u8f91\u5206\u6790\uff1b\u89e3\u51b3\u667a\u80fd\u4f53\u5f00\u53d1\u8fc7\u7a0b\u4e2d\u7684\u6280\u672f\u548c\u4e1a\u52a1\u95ee\u9898\uff1b\u4e0e\u4e1a\u52a1\u90e8\u95e8\u6c9f\u901a\uff1b\u8981\u6c42\u4e86\u89e3 RAG\u3001Prompt\u3001MCP \u7b49\u4eba\u5de5\u667a\u80fd\u9886\u57df\u6280\u672f\u3002",
            "url": "https://job.zhizuobiao.com/gwai000222.html",
            "fetch_status": "public_search_result_fallback",
        },
        {
            "title": "Agent\u5f00\u53d1\u5de5\u7a0b\u5e08",
            "location": "\u5317\u4eac/\u6df1\u5733/\u8fdc\u7a0b",
            "salary_or_benefits": "\u85aa\u8d44\u9762\u8bae\uff1b\u7075\u6d3b\u529e\u516c\u3001\u533b\u7597\u4fdd\u9669\u3001\u5e74\u5ea6\u4f53\u68c0\u3001\u5b66\u4e60\u53d1\u5c55\u3001\u80a1\u6743\u6fc0\u52b1",
            "source_channel": "DTNS\u5b98\u7f51",
            "description_requirements": "\u8d1f\u8d23\u667a\u80fd Agent \u7cfb\u7edf\u8bbe\u8ba1\u548c\u5f00\u53d1\uff0c\u6784\u5efa\u80fd\u81ea\u4e3b\u5b8c\u6210\u590d\u6742\u4efb\u52a1\u7684\u667a\u80fd\u4f53\uff1b\u672c\u79d1\u53ca\u4ee5\u4e0a\uff0c2\u5e74\u4ee5\u4e0a Python/Java \u5f00\u53d1\u7ecf\u9a8c\uff1b\u719f\u6089\u591a Agent \u7cfb\u7edf\u67b6\u6784\u548c\u5f00\u53d1\u6a21\u5f0f\uff0c\u4e86\u89e3\u5f3a\u5316\u5b66\u4e60\u548c\u51b3\u7b56\u89c4\u5212\u7b97\u6cd5\uff0c\u6709 AutoGPT/LangChain \u6846\u67b6\u7ecf\u9a8c\u4f18\u5148\u3002",
            "url": "https://www.dtns.top/join.html",
            "fetch_status": "parsed_text_supplement",
        },
    ]
    for row in supplemental:
        key = (row["title"], row["url"])
        if key not in seen:
            rows.append(row)
            seen.add(key)

    cleaned = []
    for row in rows:
        if not row.get("title"):
            continue
        if row["source_channel"] == "DTNS\u5b98\u7f51" and row["title"] == "\u52a0\u5165DTNS.OS\u56e2\u961f":
            continue
        if row["source_channel"] == "\u804c\u5750\u6807" and row["fetch_status"] == "parsed_text":
            row["title"] = "AI\u667a\u80fd\u4f53\u5f00\u53d1\u5de5\u7a0b\u5e08"
            row["location"] = "\u5927\u8fde"
            row["salary_or_benefits"] = "10-15K\uff1b\u798f\u5229\u89c1\u804c\u4f4d\u9875\u9762"
        if row["source_channel"] == "\u8fdc\u7a0b\u5de5\u4f5c\u8005":
            row["location"] = "\u90d1\u5dde/\u5e7f\u5dde/\u8fdc\u7a0b"
            row["salary_or_benefits"] = "20-40k\uff1b\u85aa\u8d44+\u5956\u91d1+\u671f\u6743/\u80a1\u7968\uff0c\u798f\u5229\u4f53\u7cfb\u4e0e\u804c\u4e1a\u53d1\u5c55\u652f\u6301"
        if row["source_channel"] == "\u5ce8\u7709\u5c71\u4eba\u624d\u7f51":
            row["title"] = "AI\u667a\u80fd\u4f53\u5f00\u53d1\u5de5\u7a0b\u5e08"
            row["salary_or_benefits"] = "\u85aa\u8d44\u9762\u8bae"
        if row["source_channel"] == "Ashby" and row["salary_or_benefits"] == "$65":
            row["salary_or_benefits"] = "\u672a\u516c\u5f00/\u672a\u63d0\u53d6\u5230"
        cleaned.append(row)

    deduped = []
    seen_keys = set()
    for row in cleaned:
        key = (row["source_channel"], row["title"], row["location"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)
    rows = deduped
    fields = ["title", "location", "salary_or_benefits", "source_channel", "description_requirements", "url", "fetch_status"]
    csv_path = OUT / "agent_developer_jobs.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean_text(row.get(field, "")) for field in fields})

    source_counts = {}
    for row in rows:
        source_counts[row["source_channel"]] = source_counts.get(row["source_channel"], 0) + 1

    summary = {
        "generated_at": "2026-05-07",
        "query": "agent developer jobs / AI Agent Engineer / Agent development roles",
        "csv": str(csv_path),
        "record_count": len(rows),
        "source_counts": source_counts,
        "fetch_log": fetch_log,
        "blocked_or_skipped_platforms": BLOCKED_PLATFORMS,
        "notes": [
            "\u53ea\u91c7\u96c6\u516c\u5f00\u53ef\u8bbf\u95ee\u9875\u9762\uff1b\u4e0d\u7ed5\u8fc7\u9a8c\u8bc1\u7801\u3001\u767b\u5f55\u5899\u6216 robots \u7981\u6b62\u3002",
            "\u4f18\u5148\u89e3\u6790 JobPosting JSON-LD\uff1b\u65e0\u7ed3\u6784\u5316\u6570\u636e\u65f6\u4f7f\u7528\u6b63\u6587\u89c4\u5219\u62bd\u53d6\u3002",
            "fetch_status=public_search_result_fallback means the page was not stable through direct fetch and public indexed snippets/page-visible info were used as a marked fallback.",
        ],
        "sample": rows[:5],
    }
    summary_path = OUT / "agent_developer_jobs_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"csv": str(csv_path), "summary": str(summary_path), "count": len(rows), "source_counts": source_counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
