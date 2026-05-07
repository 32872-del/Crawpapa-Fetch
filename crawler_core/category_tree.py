"""Category tree discovery from navigation links and sitemap sources."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup


SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
DEFAULT_EXCLUDE_FIRST = {
    "advies", "blog", "blogs", "inspiratie", "merken", "brand", "brands",
    "service", "klantenservice", "customer-service", "winkel", "winkels",
    "store", "stores", "cart", "checkout", "account", "mijn-etos",
    "search", "zoeken", "folder", "folders", "nieuws", "pers",
    "100jaar", "aanbiedingen", "acties", "actievoorwaarden", "about-us",
    "advies-inspiratie", "andc", "bezorgen-op-vakantieadres",
    "community-richtlijnen", "dagjeweg", "folder-acties", "https:",
    "international", "keurmerk-zelfzorg-online", "kwetsbaarheid-melden",
    "over-etos", "persbericht-leidschendam", "persevent", "social-media",
    "specialisten", "terugroepacties-en-veiligheidswaarschuwingen",
    "toegankelijkheidsverklaring", "vouchers", "zakelijk",
}
DEFAULT_NOISE_RE = re.compile(
    r"(dummy|test|old|preview|actievoorwaarden|privacy|cookies|about-us|over-etos|"
    r"toegankelijkheidsverklaring|kwetsbaarheid|community|zakelijk|international|"
    r"persbericht|persevent|social-media|loyalty|eerdergekocht)",
    re.I,
)
PRODUCT_URL_RE = re.compile(r"(\.html$|/producten/|/product/|/p/|pid=|sku=)", re.I)


def build_category_tree(
    base_url: str,
    sitemap_index_xml: str = "",
    category_sitemap_xml: str = "",
    product_sitemap_xml: str = "",
    nav_html: str = "",
    max_depth: int = 3,
    exclude_first: set[str] | None = None,
) -> dict[str, Any]:
    max_depth = max(1, min(int(max_depth), 6))
    exclude = exclude_first or DEFAULT_EXCLUDE_FIRST
    sitemap_sources = _sitemap_sources(sitemap_index_xml, base_url)
    category_urls = _extract_urlset_locs(category_sitemap_xml)
    product_urls = _extract_urlset_locs(product_sitemap_xml)
    nav_urls = _extract_nav_urls(nav_html, base_url)

    if not category_urls:
        category_urls = [
            item["url"] for item in sitemap_sources
            if item["kind"] == "category_candidate"
        ]
    if not product_urls:
        product_urls = [
            item["url"] for item in sitemap_sources
            if item["kind"] == "product_candidate"
        ]

    raw_urls = category_urls or nav_urls
    candidate_paths = []
    empty_removed = []
    product_prefixes = _product_prefixes(product_urls, max_depth=max_depth)
    can_filter_empty = bool(product_prefixes)
    for raw_url in raw_urls:
        path_parts = _category_parts(raw_url, base_url, max_depth, exclude)
        if not path_parts:
            continue
        canonical = _url_for_parts(base_url, path_parts)
        if can_filter_empty and not _has_product_under(path_parts, product_prefixes):
            empty_removed.append(canonical)
            continue
        candidate_paths.append(tuple(path_parts))

    tree = _paths_to_tree(sorted(set(candidate_paths)), base_url)
    return {
        "source": {
            "base_url": base_url,
            "sitemap_sources": sitemap_sources,
            "used": "category_sitemap" if category_sitemap_xml else "navigation_or_sitemap_index",
        },
        "max_depth": max_depth,
        "top_count": len(tree),
        "categories": tree,
        "coverage": {
            "category_url_count": len(category_urls),
            "nav_url_count": len(nav_urls),
            "product_sitemap_count": len(product_urls),
            "product_prefix_count": len(product_prefixes),
            "full_product_coverage_likely": bool(product_urls),
            "empty_category_filter_supported": can_filter_empty,
            "empty_filter_method": "product_sitemap_prefix_match" if can_filter_empty else "not_available",
            "method": "category_sitemap_plus_product_sitemap" if product_urls else "category_source_only",
            "note": (
                "Product sitemap URLs do not expose category prefixes; directory tree is preserved and "
                "full product coverage should be driven by the product sitemap."
                if product_urls and not can_filter_empty else ""
            ),
        },
        "empty_candidates_removed": len(empty_removed),
        "empty_candidate_samples": empty_removed[:20],
    }


def pick_sitemap_urls(index_xml: str, base_url: str) -> dict[str, str]:
    sources = _sitemap_sources(index_xml, base_url)
    category = next((item["url"] for item in sources if item["kind"] == "category_candidate"), "")
    product = next((item["url"] for item in sources if item["kind"] == "product_candidate"), "")
    return {"category_sitemap_url": category, "product_sitemap_url": product}


def _sitemap_sources(index_xml: str, base_url: str) -> list[dict[str, str]]:
    if not index_xml:
        return []
    sources = []
    try:
        root = ET.fromstring(index_xml)
    except ET.ParseError:
        return []
    for loc in root.findall(".//sm:loc", SITEMAP_NS):
        if not loc.text:
            continue
        url = urljoin(base_url, loc.text.strip())
        lowered = url.lower()
        if "category" in lowered or "categorie" in lowered:
            kind = "category_candidate"
        elif "product" in lowered:
            kind = "product_candidate"
        else:
            kind = "other"
        sources.append({"url": url, "kind": kind})
    return sources


def _extract_urlset_locs(xml_text: str) -> list[str]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [loc.text.strip() for loc in root.findall(".//sm:loc", SITEMAP_NS) if loc.text]


def _extract_nav_urls(html: str, base_url: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for link in soup.select("nav a[href], header a[href], a[href]"):
        href = str(link.get("href", "")).strip()
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
            urls.append(urljoin(base_url, href))
    return urls


def _category_parts(url: str, base_url: str, max_depth: int, exclude_first: set[str]) -> list[str]:
    parsed = urlparse(url)
    base_host = urlparse(base_url).netloc
    if base_host and parsed.netloc and parsed.netloc != base_host:
        return []
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if not parts:
        return []
    if PRODUCT_URL_RE.search(parsed.path):
        return []
    if parts[0].lower() in exclude_first:
        return []
    if DEFAULT_NOISE_RE.search(parsed.path):
        return []
    return parts[:max_depth]


def _product_prefixes(product_urls: list[str], max_depth: int) -> set[tuple[str, ...]]:
    prefixes = set()
    for url in product_urls:
        parts = [unquote(part) for part in urlparse(url).path.strip("/").split("/") if part]
        if not parts:
            continue
        # Product URL sitemaps often do not include categories. Keep prefixes only
        # when there is path hierarchy before a product-looking leaf.
        if len(parts) > 2 and PRODUCT_URL_RE.search(parts[-1]):
            for depth in range(1, min(max_depth, len(parts) - 1) + 1):
                prefixes.add(tuple(parts[:depth]))
    return prefixes


def _has_product_under(parts: list[str], product_prefixes: set[tuple[str, ...]]) -> bool:
    if not product_prefixes:
        return True
    candidate = tuple(parts)
    return any(prefix[:len(candidate)] == candidate or candidate[:len(prefix)] == prefix for prefix in product_prefixes)


def _paths_to_tree(paths: list[tuple[str, ...]], base_url: str) -> list[dict[str, Any]]:
    roots = []
    index: dict[tuple[str, ...], dict[str, Any]] = {}
    children_by_key: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        for depth in range(1, len(path) + 1):
            key = path[:depth]
            if key in index:
                continue
            node = {"name": _label(key[-1]), "url": _url_for_parts(base_url, list(key)), "children": []}
            index[key] = node
            if depth == 1:
                roots.append(node)
            else:
                parent = index[key[:-1]]
                parent["children"].append(node)
    return [_prune(node) for node in roots]


def _url_for_parts(base_url: str, parts: list[str] | tuple[str, ...]) -> str:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return root + "/" + "/".join(parts) + "/"


def _label(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _prune(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("children"):
        node["children"] = [_prune(child) for child in node["children"]]
    else:
        node.pop("children", None)
    return node
