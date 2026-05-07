"""Access diagnostics for legitimate crawler configuration.

The goal is to explain why a page did not extract cleanly and suggest safer
collection strategies: browser rendering, waits, scrolling, structured data,
or authenticated session reuse. It intentionally does not automate CAPTCHA or
access-control bypasses.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from crawler_core.challenge import detect_in_html


API_URL_RE = re.compile(
    r"""(?:"|')(?P<url>(?:https?:)?//[^"']+|/[^"']*?(?:api|graphql|ajax|search|product|products|catalog)[^"']*?)(?:"|')""",
    re.IGNORECASE,
)


def diagnose_html(html: str, url: str = "", target_selector: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    text_len = len(soup.get_text(" ", strip=True))
    scripts = soup.find_all("script")
    challenge = detect_in_html(html or "")
    target_count = len(soup.select(target_selector)) if target_selector else 0
    jsonld_count = len(soup.find_all("script", type="application/ld+json"))
    next_data = soup.select_one("script#__NEXT_DATA__")
    nuxt_data = bool(re.search(r"window\.__NUXT__|__NUXT_DATA__", html or ""))
    app_roots = len(soup.select("#root, #app, [data-reactroot], [ng-version]"))
    api_hints = _extract_api_hints(html or "")

    signals = {
        "url": url,
        "html_bytes": len(html or ""),
        "text_chars": text_len,
        "script_count": len(scripts),
        "jsonld_count": jsonld_count,
        "next_data": bool(next_data),
        "nuxt_data": nuxt_data,
        "app_root_count": app_roots,
        "target_selector": target_selector,
        "target_count": target_count,
        "challenge": challenge,
        "api_hints": api_hints[:20],
    }

    findings: list[str] = []
    recommendations: list[dict[str, Any]] = []

    if challenge:
        findings.append(f"challenge_detected:{challenge}")
        recommendations.append({
            "type": "manual_intervention",
            "reason": "The page looks like a CAPTCHA, human verification, or managed challenge.",
            "action": "Use a permitted API, obtain explicit access, or save a valid cookie profile after manual login/verification.",
        })

    if target_selector and target_count == 0:
        findings.append("target_selector_missed")
        recommendations.append({
            "type": "selector_tuning",
            "reason": "The requested selector did not match the fetched HTML.",
            "action": "Use browser rendering, inspect the rendered DOM, or choose a selector visible in the returned HTML.",
        })

    if _looks_like_js_shell(signals):
        findings.append("js_rendering_likely_required")
        recommendations.append({
            "type": "browser_rendering",
            "reason": "The page has little text with many scripts/app roots, which often means client-side rendering.",
            "action": {
                "mode": "browser",
                "wait_until": "networkidle",
                "render_time": 5,
                "scroll_count": 2,
            },
        })

    if jsonld_count or next_data or nuxt_data:
        findings.append("embedded_structured_data_available")
        recommendations.append({
            "type": "structured_data",
            "reason": "The page contains JSON-LD or framework data that is usually more stable than CSS selectors.",
            "action": "Prefer JSON-LD/__NEXT_DATA__/__NUXT__ parsing for product fields where possible.",
        })

    if api_hints:
        findings.append("possible_api_endpoints_found")
        recommendations.append({
            "type": "network_api_review",
            "reason": "Scripts reference API-like URLs that may expose the same public data in structured form.",
            "action": "Inspect the endpoint manually and use fetch_json when it is permitted and stable.",
        })

    if not recommendations:
        recommendations.append({
            "type": "standard",
            "reason": "No strong challenge or JS shell signal was found.",
            "action": {"mode": "auto", "use_cache": True},
        })

    return {
        "ok": not bool(challenge),
        "findings": findings,
        "signals": signals,
        "recommendations": recommendations,
    }


def _looks_like_js_shell(signals: dict[str, Any]) -> bool:
    if signals["challenge"]:
        return False
    if signals["text_chars"] < 500 and signals["script_count"] >= 8:
        return True
    if signals["app_root_count"] and signals["text_chars"] < 1200:
        return True
    return False


def _extract_api_hints(html: str) -> list[str]:
    seen = set()
    result = []
    for match in API_URL_RE.finditer(html[:500_000]):
        raw = match.group("url")
        if raw in seen:
            continue
        seen.add(raw)
        result.append(raw)
    return result
