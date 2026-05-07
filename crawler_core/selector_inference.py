"""Infer ranked CSS selector candidates from HTML samples."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


PRICE_RE = re.compile(r"(?:[$€£¥]|USD|EUR|GBP|PLN|RMB|CNY)?\s*\d[\d\s.,]*(?:[$€£¥]|USD|EUR|GBP|PLN|RMB|CNY)?", re.I)
IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", re.I)
PRODUCT_WORD_RE = re.compile(r"(product|item|goods|detail|sku|catalog|p/|prod)", re.I)
NOISE_CLASS_RE = re.compile(r"^(active|disabled|selected|current|open|show|hide|loaded|lazy|swiper|slick)$", re.I)


def infer_selector_candidates(
    html: str,
    base_url: str = "",
    target_fields: list[str] | None = None,
    max_candidates: int = 8,
) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    fields = target_fields or ["list_link", "title", "price", "image_src", "body"]
    result: dict[str, Any] = {"fields": {}, "best_spec_fragment": {}}
    for field in fields:
        candidates = _infer_for_field(soup, field, base_url)
        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        deduped = _dedupe_candidates(ranked)[:max_candidates]
        result["fields"][field] = deduped
        if deduped:
            if field == "list_link":
                result["best_spec_fragment"].setdefault("list", {})["item_link"] = deduped[0]["selector"]
            elif field == "image_src":
                result["best_spec_fragment"].setdefault("detail", {})["image_src"] = deduped[0]["selector"]
            else:
                result["best_spec_fragment"].setdefault("detail", {})[field] = deduped[0]["selector"]
    return result


def infer_site_spec_from_samples(
    list_html: str,
    detail_htmls: list[tuple[str, str]],
    base_url: str,
    site: str,
    goal: str = "",
    mode: str = "auto",
    target_fields: list[str] | None = None,
    max_candidates: int = 8,
) -> dict[str, Any]:
    """Infer a draft site_spec by voting selectors across multiple detail pages."""
    fields = target_fields or ["title", "price", "image_src", "body"]
    list_result = infer_selector_candidates(
        list_html,
        base_url=base_url,
        target_fields=["list_link"],
        max_candidates=max_candidates,
    )
    list_candidates = list_result["fields"].get("list_link", [])
    list_selector = list_candidates[0]["selector"] if list_candidates else ""

    field_votes: dict[str, Counter[str]] = {field: Counter() for field in fields}
    field_examples: dict[str, dict[str, list[str]]] = {field: {} for field in fields}
    per_page: list[dict[str, Any]] = []
    for detail_url, detail_html in detail_htmls:
        inferred = infer_selector_candidates(
            detail_html,
            base_url=detail_url,
            target_fields=fields,
            max_candidates=max_candidates,
        )
        page_summary = {"url": detail_url, "fields": {}}
        for field in fields:
            candidates = inferred["fields"].get(field, [])
            if not candidates:
                page_summary["fields"][field] = []
                continue
            top = candidates[0]
            selector = top["selector"]
            field_votes[field][selector] += 1
            field_examples[field].setdefault(selector, [])
            field_examples[field][selector].extend(top.get("sample", [])[:2])
            page_summary["fields"][field] = candidates[:3]
        per_page.append(page_summary)

    detail_spec: dict[str, str] = {}
    confidence: dict[str, dict[str, Any]] = {}
    sample_count = max(1, len(detail_htmls))
    for field, votes in field_votes.items():
        if not votes:
            confidence[field] = {
                "selector": "",
                "score": 0,
                "votes": 0,
                "samples": [],
            }
            continue
        selector, vote_count = votes.most_common(1)[0]
        detail_spec[field] = selector
        confidence[field] = {
            "selector": selector,
            "score": round(vote_count / sample_count, 3),
            "votes": vote_count,
            "samples": field_examples[field].get(selector, [])[:5],
        }

    spec = {
        "version": "1.0",
        "site": site,
        "goal": goal,
        "mode": mode,
        "start_urls": [{"url": base_url}],
        "pagination": {},
        "list": {"item_link": list_selector},
        "detail": detail_spec,
        "variants": {},
        "dedupe": ["categories_1", "categories_2", "categories_3", "url"],
        "required_fields": ["handle", "title", "image_src", "price"],
    }
    overall = _overall_confidence(list_candidates, confidence, sample_count)
    return {
        "spec": spec,
        "confidence": {
            "overall": overall,
            "list_link": {
                "selector": list_selector,
                "score": min(1.0, (list_candidates[0]["count"] / max(1, len(detail_htmls))) if list_candidates else 0),
                "candidates": list_candidates[:max_candidates],
            },
            "fields": confidence,
            "sample_count": len(detail_htmls),
        },
        "per_page": per_page,
        "recommendation": "ready_to_validate" if overall >= 0.7 else "needs_review",
    }


def _infer_for_field(soup: BeautifulSoup, field: str, base_url: str) -> list[dict[str, Any]]:
    if field == "list_link":
        return _link_candidates(soup, base_url)
    if field == "title":
        return _text_candidates(soup, ["h1", "h2", "[class*=title]", "[class*=name]", "[itemprop=name]"], field)
    if field == "price":
        return _price_candidates(soup)
    if field == "image_src":
        return _image_candidates(soup, base_url)
    if field == "body":
        return _text_candidates(soup, ["[class*=description]", "[class*=overview]", "[class*=content]", "article", "section"], field)
    return _text_candidates(soup, [f"[class*={field}]", f"[itemprop={field}]"], field)


def _overall_confidence(list_candidates: list[dict[str, Any]], confidence: dict[str, dict[str, Any]], sample_count: int) -> float:
    if not list_candidates:
        list_score = 0.0
    else:
        list_score = min(1.0, list_candidates[0].get("count", 0) / max(1, sample_count))
    field_scores = [item.get("score", 0) for item in confidence.values()]
    if not field_scores:
        return round(list_score, 3)
    return round((list_score + sum(field_scores) / len(field_scores)) / 2, 3)


def _link_candidates(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", "")).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        selector = _selector_for(link) + "@href"
        groups.setdefault(selector, []).append(urljoin(base_url, href))

    candidates = []
    for selector, urls in groups.items():
        unique_urls = sorted(set(urls))
        productish = sum(1 for item in unique_urls if PRODUCT_WORD_RE.search(item))
        same_domain = _same_domain_ratio(unique_urls, base_url)
        score = len(unique_urls) * 5 + productish * 3 + same_domain * 4 + _selector_stability_score(selector)
        candidates.append({
            "selector": selector,
            "score": round(score, 3),
            "count": len(unique_urls),
            "sample": unique_urls[:3],
            "reason": "link_count+product_url_hint+domain_consistency",
        })
    return candidates


def _text_candidates(soup: BeautifulSoup, selector_seeds: list[str], field: str) -> list[dict[str, Any]]:
    candidates = []
    seen_tags: set[int] = set()
    for seed in selector_seeds:
        for tag in soup.select(seed):
            if not isinstance(tag, Tag) or id(tag) in seen_tags:
                continue
            seen_tags.add(id(tag))
            text = tag.get_text(" ", strip=True)
            if not _useful_text(text):
                continue
            selector = _selector_for(tag)
            length_score = min(len(text), 120) / 12
            heading_bonus = 8 if tag.name in {"h1", "h2"} else 0
            field_bonus = 5 if _field_hint(selector, field) else 0
            score = length_score + heading_bonus + field_bonus + _selector_stability_score(selector)
            candidates.append({
                "selector": selector,
                "score": round(score, 3),
                "count": len(soup.select(selector)),
                "sample": [text[:160]],
                "reason": "text_quality+semantic_tag+field_name_hint",
            })
    return candidates


def _price_candidates(soup: BeautifulSoup) -> list[dict[str, Any]]:
    candidates = []
    for tag in soup.select("[class*=price], [itemprop=price], [data-price], meta[property*=price], meta[itemprop=price]"):
        value = tag.get("content") or tag.get("data-price") or tag.get_text(" ", strip=True)
        if not value or not PRICE_RE.search(str(value)):
            continue
        selector = _selector_for(tag)
        score = 12 + _selector_stability_score(selector)
        if "price" in selector.lower():
            score += 8
        candidates.append({
            "selector": selector if tag.name != "meta" else selector + "@content",
            "score": round(score, 3),
            "count": len(soup.select(selector)),
            "sample": [str(value)[:80]],
            "reason": "price_pattern+price_attribute_or_class",
        })
    return candidates


def _image_candidates(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    candidates = []
    for tag in soup.select("img[src], img[data-src], source[srcset], meta[property='og:image'], meta[itemprop=image]"):
        attr = "content" if tag.name == "meta" else "srcset" if tag.name == "source" else "data-src" if tag.get("data-src") else "src"
        value = str(tag.get(attr, "")).strip()
        if not value:
            continue
        selector = _selector_for(tag) + f"@{attr}"
        image_hint = 8 if IMAGE_EXT_RE.search(value) else 0
        product_hint = 5 if re.search(r"(product|catalog|goods|image|photo|gallery)", selector + value, re.I) else 0
        score = image_hint + product_hint + _selector_stability_score(selector)
        candidates.append({
            "selector": selector,
            "score": round(score, 3),
            "count": len(soup.select(selector.split("@", 1)[0])),
            "sample": [urljoin(base_url, value)[:180]],
            "reason": "image_url_hint+gallery_or_product_hint",
        })
    return candidates


def _selector_for(tag: Tag) -> str:
    if tag.get("id"):
        return f"{tag.name}#{_css_escape(str(tag['id']))}"
    classes = [cls for cls in tag.get("class", []) if not NOISE_CLASS_RE.match(str(cls))]
    if classes:
        return tag.name + "".join(f".{_css_escape(str(cls))}" for cls in classes[:2])
    for attr in ("itemprop", "data-testid", "data-test", "aria-label"):
        if tag.get(attr):
            return f"{tag.name}[{attr}='{_quote_attr(str(tag[attr]))}']"
    return tag.name


def _selector_stability_score(selector: str) -> float:
    score = 0.0
    if "#" in selector:
        score += 7
    if "." in selector:
        score += 5
    if "itemprop" in selector or "data-test" in selector:
        score += 6
    if re.search(r"\b(css|sc|jsx)-[a-z0-9]{5,}", selector, re.I):
        score -= 6
    return score


def _field_hint(selector: str, field: str) -> bool:
    aliases = {
        "title": ["title", "name", "headline"],
        "body": ["description", "overview", "content", "detail"],
    }.get(field, [field])
    lowered = selector.lower()
    return any(alias in lowered for alias in aliases)


def _useful_text(text: str) -> bool:
    if not text or len(text.strip()) < 2:
        return False
    if len(text) > 2000:
        return False
    return True


def _same_domain_ratio(urls: list[str], base_url: str) -> float:
    if not urls or not base_url:
        return 0.0
    base_host = urlparse(base_url).netloc
    if not base_host:
        return 0.0
    return sum(1 for item in urls if urlparse(item).netloc == base_host) / len(urls)


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in candidates:
        key = item["selector"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _css_escape(value: str) -> str:
    return re.sub(r"([^a-zA-Z0-9_-])", lambda m: "\\" + m.group(1), value)


def _quote_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
