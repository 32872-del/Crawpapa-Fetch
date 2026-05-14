"""Crawpapa-Fetch integration layer for vendored Scrapling.

The upstream Scrapling source is vendored under the top-level ``scrapling``
package. This module keeps MCP tools small and returns JSON-friendly payloads.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


SCRAPLING_VERSION = "0.4.8"


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def get_scrapling_status() -> dict[str, Any]:
    dependencies = {
        "lxml": _module_available("lxml"),
        "cssselect": _module_available("cssselect"),
        "orjson": _module_available("orjson"),
        "tld": _module_available("tld"),
        "w3lib": _module_available("w3lib"),
        "curl_cffi": _module_available("curl_cffi"),
        "playwright": _module_available("playwright"),
        "patchright": _module_available("patchright"),
        "browserforge": _module_available("browserforge"),
        "apify_fingerprint_datapoints": _module_available("apify_fingerprint_datapoints"),
        "msgspec": _module_available("msgspec"),
        "protego": _module_available("protego"),
    }
    return {
        "vendored": True,
        "version": SCRAPLING_VERSION,
        "package_importable": _module_available("scrapling"),
        "features": {
            "selector_parser": dependencies["lxml"] and dependencies["cssselect"] and dependencies["orjson"],
            "adaptive_selectors": dependencies["lxml"] and dependencies["orjson"],
            "static_fetcher": dependencies["curl_cffi"],
            "dynamic_fetcher": dependencies["playwright"],
            "stealthy_fetcher": dependencies["patchright"] and dependencies["browserforge"],
            "spider_framework": dependencies["protego"],
        },
        "dependencies": dependencies,
        "license": "BSD-3-Clause",
        "notice_file": "THIRD_PARTY_NOTICES.md",
    }


def _selector_to_record(selector: Any, base_url: str = "") -> dict[str, Any]:
    attrs = dict(getattr(selector, "attrib", {}) or {})
    text = ""
    try:
        text = str(selector.get_all_text(" ", strip=True))
    except Exception:
        text = str(getattr(selector, "text", "") or "")
    html = ""
    try:
        html = str(selector.get())
    except Exception:
        html = ""
    if len(html) > 2000:
        html = html[:2000]
    href = attrs.get("href", "")
    src = attrs.get("src", "")
    absolute_url = ""
    if href:
        try:
            absolute_url = selector.urljoin(href)
        except Exception:
            absolute_url = href
    elif src:
        try:
            absolute_url = selector.urljoin(src)
        except Exception:
            absolute_url = src
    return {
        "tag": getattr(selector, "tag", ""),
        "text": text,
        "attributes": attrs,
        "html": html,
        "url": absolute_url,
        "base_url": base_url,
    }


def parse_with_scrapling(
    html: str | bytes,
    selector: str,
    selector_type: str = "css",
    attr: str = "",
    url: str = "",
    adaptive: bool = False,
    auto_save: bool = False,
    identifier: str = "",
    storage_file: str = "",
    percentage: int = 40,
    max_results: int = 50,
) -> dict[str, Any]:
    from scrapling import Selector

    storage_args = {"storage_file": storage_file, "url": url} if storage_file else None
    page = Selector(html, url=url, adaptive=adaptive, storage_args=storage_args)
    if selector_type == "xpath":
        matches = page.xpath(
            selector,
            identifier=identifier or selector,
            adaptive=adaptive,
            auto_save=auto_save,
            percentage=percentage,
        )
    elif selector_type == "css":
        matches = page.css(
            selector,
            identifier=identifier or selector,
            adaptive=adaptive,
            auto_save=auto_save,
            percentage=percentage,
        )
    else:
        raise ValueError("selector_type must be css or xpath")

    values: list[Any] = []
    records: list[dict[str, Any]] = []
    for item in list(matches)[: max(1, min(int(max_results), 500))]:
        records.append(_selector_to_record(item, base_url=url))
        if attr:
            value = dict(getattr(item, "attrib", {}) or {}).get(attr, "")
            if attr in {"href", "src"} and value:
                try:
                    value = item.urljoin(value)
                except Exception:
                    pass
            values.append(value)
        else:
            try:
                values.append(str(item.get_all_text(" ", strip=True)))
            except Exception:
                values.append(str(item.get()))
    return {
        "ok": True,
        "engine": "scrapling",
        "selector_type": selector_type,
        "selector": selector,
        "count": len(matches),
        "returned": len(records),
        "values": values,
        "records": records,
        "adaptive": adaptive,
        "auto_save": auto_save,
        "identifier": identifier or selector,
        "storage_file": storage_file,
    }


def find_similar_with_scrapling(
    html: str | bytes,
    seed_selector: str,
    selector_type: str = "css",
    url: str = "",
    similarity_threshold: float = 0.2,
    match_text: bool = False,
    max_results: int = 50,
) -> dict[str, Any]:
    from scrapling import Selector

    page = Selector(html, url=url)
    matches = page.xpath(seed_selector) if selector_type == "xpath" else page.css(seed_selector)
    seed = matches.first
    if not seed:
        return {
            "ok": False,
            "engine": "scrapling",
            "selector": seed_selector,
            "selector_type": selector_type,
            "count": 0,
            "records": [],
            "message": "Seed selector did not match any element.",
        }
    similar = seed.find_similar(similarity_threshold=similarity_threshold, match_text=match_text)
    records = [_selector_to_record(item, base_url=url) for item in list(similar)[: max(1, min(int(max_results), 500))]]
    return {
        "ok": True,
        "engine": "scrapling",
        "seed": _selector_to_record(seed, base_url=url),
        "selector": seed_selector,
        "selector_type": selector_type,
        "count": len(similar),
        "returned": len(records),
        "records": records,
    }


def fetch_with_scrapling(
    url: str,
    mode: str = "static",
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str = "",
    timeout: int = 30000,
    wait: int = 0,
    wait_selector: str = "",
    network_idle: bool = False,
    disable_resources: bool = False,
    headless: bool = True,
    proxy: str = "",
) -> dict[str, Any]:
    headers = headers or {}
    mode = (mode or "static").lower()
    method = (method or "GET").upper()
    static_timeout = timeout / 1000 if timeout and timeout > 1000 else timeout
    kwargs: dict[str, Any] = {
        "headers": headers or None,
        "timeout": static_timeout,
    }
    if proxy:
        kwargs["proxy"] = proxy

    if mode == "static":
        from scrapling.fetchers.requests import Fetcher

        if method == "POST":
            response = Fetcher.post(url, data=body or None, **kwargs)
        elif method == "PUT":
            response = Fetcher.put(url, data=body or None, **kwargs)
        elif method == "DELETE":
            response = Fetcher.delete(url, **kwargs)
        else:
            response = Fetcher.get(url, **kwargs)
    elif mode == "dynamic":
        from scrapling.fetchers import DynamicFetcher

        response = DynamicFetcher.fetch(
            url,
            headless=headless,
            wait=wait,
            wait_selector=wait_selector or None,
            network_idle=network_idle,
            disable_resources=disable_resources,
            timeout=timeout,
            proxy=proxy or None,
            extra_headers=headers or None,
        )
    else:
        raise ValueError("mode must be static or dynamic")

    body_bytes = getattr(response, "body", b"")
    if isinstance(body_bytes, bytes):
        html = body_bytes.decode(getattr(response, "encoding", "utf-8") or "utf-8", errors="replace")
    else:
        html = str(body_bytes)
    return {
        "ok": True,
        "engine": "scrapling",
        "mode": mode,
        "url": getattr(response, "url", url),
        "status": getattr(response, "status", 0),
        "reason": getattr(response, "reason", ""),
        "encoding": getattr(response, "encoding", ""),
        "html": html,
        "html_bytes": len(html.encode("utf-8", errors="ignore")),
    }


def default_scrapling_storage_path(root: str | Path, name: str = "adaptive_selectors.sqlite") -> str:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    return str(root_path / name)
