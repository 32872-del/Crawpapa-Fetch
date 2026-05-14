"""JSON-driven adapter for Crawpapa-Fetch's vendored Scrapling spider layer.

The upstream spider framework is class based. This module exposes the useful
parts (queue, scheduler, robots, sitemap, checkpoint, rule following) through a
small JSON specification so an Agent can run a real crawl without generating a
Python subclass on disk.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crawler_core.security import validate_url
from crawler_core.scrapling_adapter import SCRAPLING_VERSION


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def get_scrapling_spider_status() -> dict[str, Any]:
    deps = {
        "anyio": _module_available("anyio"),
        "curl_cffi": _module_available("curl_cffi"),
        "lxml": _module_available("lxml"),
        "orjson": _module_available("orjson"),
        "protego": _module_available("protego"),
        "msgspec": _module_available("msgspec"),
        "playwright": _module_available("playwright"),
        "patchright": _module_available("patchright"),
        "browserforge": _module_available("browserforge"),
    }
    importable = _module_available("scrapling.spiders")
    return {
        "vendored": True,
        "version": SCRAPLING_VERSION,
        "package_importable": _module_available("scrapling"),
        "spider_importable": importable,
        "features": {
            "crawl_spider": importable and deps["anyio"] and deps["curl_cffi"],
            "sitemap_spider": importable and deps["protego"] and deps["lxml"],
            "scheduler_priority_dedup": importable,
            "robots_txt": importable and deps["protego"],
            "checkpoint_resume": importable,
            "response_cache": importable,
            "dynamic_sessions_available": deps["playwright"],
            "stealth_sessions_available": deps["patchright"] and deps["browserforge"],
        },
        "dependencies": deps,
        "safety": {
            "captcha_solving": False,
            "login_bypass": False,
            "private_targets_blocked_by_default": True,
        },
    }


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item)]


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _float(value: Any, default: float = 0.0, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "scrapling_spider").strip("._")
    return cleaned[:80] or "scrapling_spider"


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


@dataclass
class FieldSpec:
    selector: str
    selector_type: str = "css"
    attr: str = ""
    many: bool = False
    default: Any = ""
    join: str = " "
    required: bool = False


@dataclass
class RunnerRule:
    extractor: Any
    priority: int | None = None
    callback: str = "parse"


def _normalize_field(value: Any) -> FieldSpec:
    if isinstance(value, str):
        selector = value
        attr = ""
        if "@" in selector and not selector.startswith(("xpath:", "css:")):
            selector, attr = selector.rsplit("@", 1)
        selector_type = "css"
        if selector.startswith("xpath:"):
            selector_type = "xpath"
            selector = selector[len("xpath:") :]
        elif selector.startswith("css:"):
            selector = selector[len("css:") :]
        return FieldSpec(selector=selector.strip(), selector_type=selector_type, attr=attr.strip())
    if isinstance(value, dict):
        selector = str(value.get("selector") or value.get("css") or value.get("xpath") or "")
        selector_type = str(value.get("selector_type") or ("xpath" if value.get("xpath") else "css")).lower()
        attr = str(value.get("attr") or "")
        if "@" in selector and not attr and selector_type == "css":
            selector, attr = selector.rsplit("@", 1)
        if selector.startswith("xpath:"):
            selector_type = "xpath"
            selector = selector[len("xpath:") :]
        elif selector.startswith("css:"):
            selector_type = "css"
            selector = selector[len("css:") :]
        return FieldSpec(
            selector=selector.strip(),
            selector_type=selector_type,
            attr=attr.strip(),
            many=_bool(value.get("many"), False),
            default=value.get("default", ""),
            join=str(value.get("join", " ")),
            required=_bool(value.get("required"), False),
        )
    raise ValueError("item_fields values must be selector strings or field objects")


def _make_link_extractor(rule: dict[str, Any], allowed_domains: list[str]) -> Any:
    from scrapling.spiders.links import LinkExtractor

    allow_domains = _as_str_list(rule.get("allow_domains")) or allowed_domains
    return LinkExtractor(
        allow=_as_str_list(rule.get("allow")),
        deny=_as_str_list(rule.get("deny")),
        allow_domains=allow_domains,
        deny_domains=_as_str_list(rule.get("deny_domains")),
        restrict_css=_as_str_list(rule.get("restrict_css")),
        restrict_xpath=_as_str_list(rule.get("restrict_xpath")),
        tags=_as_str_list(rule.get("tags")) or ("a", "area"),
        attrs=_as_str_list(rule.get("attrs")) or ("href",),
        canonicalize=_bool(rule.get("canonicalize"), True),
        keep_fragment=_bool(rule.get("keep_fragment"), False),
    )


def _select(scope: Any, field: FieldSpec) -> Any:
    if not field.selector:
        return []
    if field.selector_type == "xpath":
        return scope.xpath(field.selector)
    if field.selector_type == "css":
        return scope.css(field.selector)
    raise ValueError(f"Unsupported selector_type: {field.selector_type}")


def _text_of(node: Any) -> str:
    try:
        return str(node.get_all_text(" ", strip=True)).strip()
    except Exception:
        try:
            return str(node.get()).strip()
        except Exception:
            return ""


def _value_of(scope: Any, field: FieldSpec) -> Any:
    matches = list(_select(scope, field))
    if not matches:
        return [] if field.many else field.default
    values: list[Any] = []
    for node in matches:
        if field.attr:
            attrs = dict(getattr(node, "attrib", {}) or {})
            value = attrs.get(field.attr, "")
            if field.attr in {"href", "src", "srcset", "data-src"} and value:
                try:
                    value = node.urljoin(value)
                except Exception:
                    pass
        else:
            value = _text_of(node)
        if value != "":
            values.append(value)
    if field.many:
        return values
    if not values:
        return field.default
    if len(values) == 1:
        return values[0]
    return field.join.join(str(item) for item in values)


def _extract_items(response: Any, item_selector: str, fields: dict[str, FieldSpec]) -> list[dict[str, Any]]:
    scopes = list(response.css(item_selector)) if item_selector else [response]
    items: list[dict[str, Any]] = []
    for scope in scopes:
        item: dict[str, Any] = {"_source_url": response.url}
        missing_required = False
        for name, field in fields.items():
            value = _value_of(scope, field)
            if field.required and (value == "" or value == [] or value is None):
                missing_required = True
            item[name] = value
        if not missing_required and any(v not in ("", [], None) for k, v in item.items() if not k.startswith("_")):
            items.append(item)
    return items


def _validate_targets(urls: list[str], allowed_domains: set[str], allow_private: bool) -> None:
    for url in urls:
        validate_url(url, allow_private=allow_private, allowed_domains=allowed_domains or None)


def run_scrapling_spider(spec: dict[str, Any]) -> dict[str, Any]:
    from scrapling.fetchers.requests import FetcherSession
    from scrapling.spiders import CrawlRule, CrawlSpider, Request, SitemapSpider
    from scrapling.spiders.session import SessionManager

    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")

    spider_type = str(spec.get("spider_type") or spec.get("type") or "crawl").lower()
    if spider_type not in {"crawl", "sitemap"}:
        raise ValueError("spider_type must be crawl or sitemap")

    name = _safe_name(str(spec.get("name") or f"crawpapa_{spider_type}"))
    allow_private = _bool(spec.get("allow_private"), False)
    start_urls = _as_str_list(spec.get("start_urls") or spec.get("start_url"))
    sitemap_urls = _as_str_list(spec.get("sitemap_urls") or spec.get("sitemap_url"))
    seed_urls = sitemap_urls if spider_type == "sitemap" else start_urls
    if not seed_urls:
        raise ValueError("start_urls or sitemap_urls is required")

    allowed_domains = set(_as_str_list(spec.get("allowed_domains")))
    if not allowed_domains:
        allowed_domains = {_domain(url) for url in seed_urls if _domain(url)}
    allowed_domains.update(urlparse(url).netloc.lower() for url in seed_urls if urlparse(url).netloc)
    _validate_targets(seed_urls, allowed_domains, allow_private)

    raw_fields = spec.get("item_fields") or spec.get("fields") or {}
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise ValueError("item_fields must be a non-empty JSON object")
    item_fields = {str(name): _normalize_field(value) for name, value in raw_fields.items()}
    item_selector = str(spec.get("item_selector") or "")
    follow_rules = [rule for rule in _as_list(spec.get("follow_rules") or spec.get("rules")) if isinstance(rule, dict)]
    max_depth = _int(spec.get("max_depth"), 1, minimum=0, maximum=100)
    max_items = _int(spec.get("max_items"), 100, minimum=1, maximum=100000)
    request_headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
    proxy = str(spec.get("proxy") or "")
    timeout = _float(spec.get("timeout"), 30.0, minimum=1.0, maximum=300.0)
    checkpoint_interval = _float(spec.get("checkpoint_interval"), 300.0, minimum=1.0, maximum=86400.0)
    crawldir = spec.get("crawldir") or spec.get("checkpoint_dir") or None
    use_checkpoint = _bool(spec.get("use_checkpoint"), bool(crawldir))
    if use_checkpoint and not crawldir:
        crawldir = str(Path("data") / "scrapling_checkpoints" / name)

    runner_rules = [
        RunnerRule(
            extractor=_make_link_extractor(rule, sorted(allowed_domains)),
            priority=rule.get("priority") if rule.get("priority") is None else _int(rule.get("priority"), 0),
            callback=str(rule.get("callback") or "parse"),
        )
        for rule in follow_rules
    ]
    class_allowed_domains = allowed_domains
    class_name = name
    class_start_urls = start_urls
    class_sitemap_urls = sitemap_urls
    class_robots_txt_obey = _bool(spec.get("robots_txt_obey"), False)
    class_development_mode = _bool(spec.get("development_mode"), False)
    class_concurrent_requests = _int(spec.get("concurrent_requests"), 4, minimum=1, maximum=64)
    class_concurrent_per_domain = _int(spec.get("concurrent_requests_per_domain"), 0, minimum=0, maximum=64)
    class_download_delay = _float(spec.get("download_delay"), 0.0, minimum=0.0, maximum=60.0)
    class_max_blocked_retries = _int(spec.get("max_blocked_retries"), 3, minimum=0, maximum=20)

    class _JsonSpiderMixin:
        allowed_domains = class_allowed_domains
        robots_txt_obey = class_robots_txt_obey
        development_mode = class_development_mode
        development_cache_dir = spec.get("development_cache_dir") or None
        concurrent_requests = class_concurrent_requests
        concurrent_requests_per_domain = class_concurrent_per_domain
        download_delay = class_download_delay
        max_blocked_retries = class_max_blocked_retries
        logging_level = 40
        name = class_name

        def configure_sessions(self, manager: SessionManager) -> None:
            session_kwargs: dict[str, Any] = {
                "headers": request_headers,
                "timeout": timeout,
                "follow_redirects": spec.get("follow_redirects", "safe"),
                "verify": _bool(spec.get("verify_tls"), True),
                "stealthy_headers": _bool(spec.get("stealthy_headers"), True),
            }
            if proxy:
                session_kwargs["proxy"] = proxy
            manager.add("default", FetcherSession(**session_kwargs))

        async def parse(self, response):
            depth = _int((response.meta or {}).get("depth"), 0, minimum=0)
            for item in _extract_items(response, item_selector, item_fields):
                yield item
            if depth >= max_depth:
                return
            for rule in runner_rules:
                callback = self.parse_detail if rule.callback == "parse_detail" else self.parse
                for url in rule.extractor.extract(response):
                    validate_url(url, allow_private=allow_private, allowed_domains=allowed_domains or None)
                    yield response.follow(url, callback=callback, priority=rule.priority, meta={"depth": depth + 1})

        async def parse_detail(self, response):
            for item in _extract_items(response, "", item_fields):
                yield item

        def rules(self):
            if runner_rules:
                return [
                    CrawlRule(
                        rule.extractor,
                        callback=self.parse_detail if rule.callback == "parse_detail" else self.parse,
                        priority=rule.priority,
                    )
                    for rule in runner_rules
                ]
            if spider_type == "sitemap":
                from scrapling.spiders.links import LinkExtractor

                return [CrawlRule(LinkExtractor(allow_domains=sorted(allowed_domains)), callback=self.parse)]
            return []

        async def on_scraped_item(self, item: dict[str, Any]):
            if self._engine and len(self._engine.items) >= max_items:
                self._engine.request_pause()
                return None
            return item

    if spider_type == "sitemap":
        class JsonSitemapSpider(_JsonSpiderMixin, SitemapSpider):
            sitemap_urls = class_sitemap_urls
            start_urls: list[str] = []

        spider = JsonSitemapSpider(crawldir=crawldir if use_checkpoint else None, interval=checkpoint_interval)
    else:
        class JsonCrawlSpider(_JsonSpiderMixin, CrawlSpider):
            start_urls = class_start_urls

            async def start_requests(self):
                for url in self.start_urls:
                    yield Request(
                        url,
                        sid=self._session_manager.default_session_id,
                        callback=self.parse,
                        meta={"depth": 0},
                        headers=dict(request_headers),
                    )

        spider = JsonCrawlSpider(crawldir=crawldir if use_checkpoint else None, interval=checkpoint_interval)

    result = spider.start(use_uvloop=False)
    stats = result.stats.to_dict()
    items = list(result.items)[:max_items]
    return {
        "ok": True,
        "engine": "scrapling_spider",
        "spider_type": spider_type,
        "name": name,
        "item_count": len(items),
        "items": items,
        "stats": stats,
        "completed": result.completed,
        "paused": result.paused,
        "crawldir": str(crawldir or ""),
        "spec_summary": {
            "start_urls": start_urls,
            "sitemap_urls": sitemap_urls,
            "allowed_domains": sorted(allowed_domains),
            "item_selector": item_selector,
            "field_names": list(item_fields.keys()),
            "follow_rule_count": len(runner_rules),
            "max_depth": max_depth,
            "max_items": max_items,
            "robots_txt_obey": class_robots_txt_obey,
            "use_checkpoint": use_checkpoint,
        },
    }
