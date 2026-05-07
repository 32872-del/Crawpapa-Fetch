"""
Unified Crawler MCP Server v5.0 - Agent-oriented crawler MCP service

v5.0 主链：
- scout_page -> draft_collection_plan -> validate_collection_plan -> execute_collection_plan
- collection_plan version/kind/assumptions/risk_flags
- menu_source_path/category_urls 目录采集
- records/dict/tree 输出格式

v4.0 升级亮点：
- 三级 auto-mode 升级（HTTP→curl_cffi→browser）+ 反爬挑战页探测 + 域名成功模式记忆
- DNS rebinding 防护：解析 IP 后绑定，防 TOCTOU
- 异步并发批抓 fetch_pages_batch（基于 httpx HTTP/2）
- XPath / JSONPath 解析（基于 parsel）
- LIKE 注入面修复（自动 escape % _）
- Bloom filter 修对：dirty flag + 阈值 flush + might_contain 真预检
- 静默吞异常清理：日志带 exc_info
- Frontier 多进程租约 CAS 抢占

核心反爬能力：
- TLS 指纹伪装 (curl_cffi) — UA/指纹自动匹配，Session 按域名复用
- 浏览器渲染 (Playwright) — 增强反检测（Canvas/WebGL/AudioContext/CDC），每域名独立 Context
- User-Agent 轮换 — 与 TLS 指纹绑定，避免特征矛盾
- 代理池管理 — 健康检查、自动剔除故障代理
- 请求重试 — 指数退避 + 尊重 Retry-After
- 请求限速 — 按域名令牌桶限速
- robots.txt 合规 — 自动检查并遵守 Crawl-delay

可用工具（42个）：
基础: fetch_page, fetch_post, fetch_json, parse_html, parse_html_advanced, extract_links, extract_text
高级: fetch_page_browser, fetch_pages_batch, crawl_list, crawl_product, scroll_and_load, take_screenshot, start_crawl_job
存储: save_to_db, save_batch_to_db, query_db, export_db, list_databases, register_table_schema
管理: set_proxy, diagnose_crawler_setup, get_crawl_status, get_recent_events, get_metrics, clear_cache, frontier_rebuild_bloom, domain_memory_stats
"""

import re
import os
import sys
import json
import time
import sqlite3
import hashlib
import logging
import threading
import atexit
import csv
import random
import contextlib
import uuid
import html as html_lib
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.robotparser import RobotFileParser
from typing import Any

import requests as _requests_lib
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

from crawler_core.config import env_int as _env_int, env_list as _env_list, load_config
from crawler_core.cache import CacheStore
from crawler_core.cookies import CookieStore
from crawler_core.events import EventLog
from crawler_core.frontier import URLFrontier
from crawler_core.security import (
    domain_matches as _core_domain_matches,
    effective_allow_private as _core_effective_allow_private,
    effective_verify_tls as _core_effective_verify_tls,
    host_addresses as _core_host_addresses,
    is_private_target as _core_is_private_target,
    validate_url as _core_validate_url,
)
from crawler_core.templates import TemplateStore, render_template
from crawler_core.site_spec import (
    draft_site_spec as _draft_site_spec,
    list_spec_versions as _list_spec_versions,
    rollback_spec_version as _rollback_spec_version,
    validate_spec_against_html as _validate_spec_against_html,
    validate_spec_shape as _validate_spec_shape,
    write_spider_package as _write_spider_package,
)
from crawler_core.access_diagnostics import diagnose_html as _diagnose_html
from crawler_core.selector_inference import (
    infer_selector_candidates as _infer_selector_candidates,
    infer_site_spec_from_samples as _infer_site_spec_from_samples,
)
from crawler_core.category_tree import (
    build_category_tree as _build_category_tree,
    pick_sitemap_urls as _pick_sitemap_urls,
)
from crawler_core import challenge as _challenge_mod
from crawler_core import dns_pin as _dns_pin_mod
from crawler_core import parsing as _parsing_mod
from crawler_core.domain_memory import DomainMemory
from crawler_core.async_http import AsyncBackend, HAS_HTTPX
from crawler_core.job_normalization import (
    load_records as _load_job_records,
    normalize_job_records as _normalize_job_records,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============ 日志系统 ============

logger = logging.getLogger("unified-crawler")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# ============ 可选依赖 ============

try:
    from curl_cffi.requests import Session as CurlCffiSession
    HAS_CURL_CFFI = True
    # 检测是否支持 resolve 参数（DNS pinning 用），新版本才有
    import inspect as _inspect_curl
    try:
        _curl_sig = _inspect_curl.signature(CurlCffiSession.request)
        CURL_CFFI_SUPPORTS_RESOLVE = "resolve" in _curl_sig.parameters
    except (ValueError, TypeError):
        CURL_CFFI_SUPPORTS_RESOLVE = False
    del _inspect_curl
except ImportError:
    HAS_CURL_CFFI = False
    CURL_CFFI_SUPPORTS_RESOLVE = False

try:
    from fake_useragent import UserAgent
    _ua = UserAgent(platforms='desktop')
    HAS_FAKE_UA = True
except ImportError:
    _ua = None
    HAS_FAKE_UA = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    import xml.etree.ElementTree as ET
    HAS_XML = True
except ImportError:
    HAS_XML = False

# ============ 配置 ============

PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_VERSION = "5.2.0"
SERVER_PROTOCOL_VERSION = ".".join(SERVER_VERSION.split(".")[:2])

CONFIG = load_config(PROJECT_ROOT, SERVER_VERSION, warn=logger.warning)
DATA_DIR = CONFIG.data_dir
OUTPUT_DIR = CONFIG.output_dir
CACHE_DIR = CONFIG.cache_dir
DB_DIR = CONFIG.db_dir
SCHEMA_DIR = CONFIG.schema_dir
LOG_DIR = CONFIG.log_dir
JOB_DIR = CONFIG.job_dir
FRONTIER_DIR = CONFIG.frontier_dir
TEMPLATE_DIR = CONFIG.template_dir
COOKIE_DIR = CONFIG.cookie_dir
PROXY_FILE = CONFIG.proxy_file
SPIDER_UVEX_ROOT = CONFIG.spider_uvex_root

FETCH_MAX_LENGTH = CONFIG.fetch_max_length
REQUEST_TIMEOUT = CONFIG.request_timeout
REQUEST_RETRY = CONFIG.request_retry
RETRY_BASE_DELAY = CONFIG.retry_base_delay
RETRY_MAX_DELAY = CONFIG.retry_max_delay
BROWSER_TIMEOUT = CONFIG.browser_timeout
BROWSER_RENDER_TIME = CONFIG.browser_render_time
CACHE_TTL = CONFIG.cache_ttl
CACHE_MAX_SIZE_MB = CONFIG.cache_max_size_mb
CACHE_PRUNE_EVERY_WRITES = CONFIG.cache_prune_every_writes
EVENT_LOG_TAIL_LINES = CONFIG.event_log_tail_lines
DB_POOL_SIZE = CONFIG.db_pool_size
FRONTIER_BLOOM_CAPACITY = CONFIG.frontier_bloom_capacity
FRONTIER_BLOOM_ERROR_RATE = CONFIG.frontier_bloom_error_rate
DEFAULT_RATE_LIMIT = CONFIG.default_rate_limit
VERIFY_TLS = CONFIG.verify_tls
RESPECT_ROBOTS = CONFIG.respect_robots
PERSIST_COOKIES = CONFIG.persist_cookies
BROWSER_HEADLESS = CONFIG.browser_headless
BROWSER_ALLOW_UNSAFE_FLAGS = CONFIG.browser_allow_unsafe_flags
DETECT_CHALLENGE_PAGES = CONFIG.detect_challenge_pages
PIN_DNS = CONFIG.pin_dns
AUTO_MODE_ESCALATION = CONFIG.auto_mode_escalation
DOMAIN_MEMORY_ENABLED = CONFIG.domain_memory_enabled
ASYNC_BATCH_DEFAULT_CONCURRENCY = CONFIG.async_batch_default_concurrency
MAX_DOMAIN_SESSIONS = CONFIG.max_domain_sessions
MAX_BROWSER_CONTEXTS = CONFIG.max_browser_contexts
ALLOW_PRIVATE_NETS = CONFIG.allow_private_nets
ALLOW_REQUEST_PRIVATE_OVERRIDE = CONFIG.allow_request_private_override
ALLOW_INSECURE_TLS_OVERRIDE = CONFIG.allow_insecure_tls_override
ALLOWED_DOMAINS = CONFIG.allowed_domains
BLOCKED_DOMAINS = CONFIG.blocked_domains

CONFIG.ensure_directories()

if not VERIFY_TLS:
    _requests_lib.packages.urllib3.disable_warnings()
_requests_lib.adapters.DEFAULT_RETRIES = 5

mcp = FastMCP("unified-crawler")

# ============ UA 与 TLS 指纹匹配 ============

IMPERSONATE_PROFILES = [
    "chrome110", "chrome120", "chrome131",
    "safari17_0", "safari18_0",
]

UA_TO_IMPERSONATE = {
    "chrome": ["chrome110", "chrome120", "chrome131"],
    "safari": ["safari17_0", "safari18_0"],
    "firefox": ["chrome120", "chrome131"],
    "edge": ["chrome120", "chrome131"],
}

def _detect_browser_from_ua(ua_str: str) -> str:
    ua_lower = ua_str.lower()
    if "edg/" in ua_lower or "edge/" in ua_lower:
        return "edge"
    if "firefox/" in ua_lower:
        return "firefox"
    if "safari/" in ua_lower and "chrome/" not in ua_lower:
        return "safari"
    return "chrome"

def _get_matching_impersonate(ua_str: str) -> str:
    browser = _detect_browser_from_ua(ua_str)
    profiles = UA_TO_IMPERSONATE.get(browser, IMPERSONATE_PROFILES[:3])
    return random.choice(profiles)

def _get_random_ua() -> str:
    if HAS_FAKE_UA and _ua:
        return _ua.random
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def _get_headers(custom_headers: str = "{}") -> tuple[dict, str]:
    """返回 (headers, ua_string)，UA 与后续 impersonate 匹配"""
    ua = _get_random_ua()
    browser = _detect_browser_from_ua(ua)
    default = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-site": "none",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "document",
        "sec-fetch-user": "?1",
    }
    if browser not in {"chrome", "edge"}:
        for key in list(default):
            if key.lower().startswith("sec-"):
                default.pop(key, None)
    try:
        default.update(json.loads(custom_headers))
    except Exception:
        pass
    return default, ua

# ============ 代理池 ============

class ProxyPool:
    def __init__(self):
        self._proxies: list[dict] = []
        self._health: dict[int, dict] = {}
        self._local_proxy: str = ""
        self._current_index = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if PROXY_FILE.exists():
            try:
                data = json.loads(PROXY_FILE.read_text(encoding="utf-8"))
                for i, p in enumerate(data.get("proxies", [])):
                    proxy_str = f"http://{p.get('username', '')}:{p.get('password', '')}@{p['host']}:{p['port']}" \
                        if p.get("username") else f"http://{p['host']}:{p['port']}"
                    self._proxies.append({"http": proxy_str, "https": proxy_str})
                    self._health[i] = self._new_health()
            except Exception as e:
                logger.warning(f"加载代理配置失败: {e}")

    def _new_health(self) -> dict:
        return {
            "success": 0,
            "fail": 0,
            "latency": 0.0,
            "disabled_until": 0.0,
            "recent": deque(maxlen=20),
        }

    def set_local_proxy(self, proxy_url: str):
        self._local_proxy = proxy_url

    def get_proxy(self) -> dict | None:
        with self._lock:
            now = time.time()
            for _ in range(len(self._proxies)):
                if not self._proxies:
                    break
                idx = self._current_index % len(self._proxies)
                self._current_index += 1
                h = self._health.get(idx, {})
                if h.get("disabled_until", 0) < now:
                    return self._proxies[idx]
            if self._local_proxy:
                return {"http": self._local_proxy, "https": self._local_proxy}
        return None

    def report_success(self, proxy: dict, latency: float):
        with self._lock:
            for idx, p in enumerate(self._proxies):
                if p == proxy:
                    health = self._health.setdefault(idx, self._new_health())
                    health["success"] += 1
                    health["latency"] = latency
                    health["recent"].append({"ok": True, "latency": latency, "ts": time.time()})
                    break

    def report_failure(self, proxy: dict):
        with self._lock:
            for idx, p in enumerate(self._proxies):
                if p == proxy:
                    health = self._health.setdefault(idx, self._new_health())
                    health["fail"] += 1
                    health["recent"].append({"ok": False, "latency": 0.0, "ts": time.time()})
                    recent = list(health["recent"])
                    consecutive_failures = 0
                    for item in reversed(recent):
                        if item["ok"]:
                            break
                        consecutive_failures += 1
                    recent_success_rate = (
                        sum(1 for item in recent if item["ok"]) / len(recent)
                        if recent else 1.0
                    )
                    if consecutive_failures >= 3 or (len(recent) >= 10 and recent_success_rate < 0.5):
                        penalty = min(60 * max(consecutive_failures, health["fail"]), 600)
                        health["disabled_until"] = time.time() + penalty
                        logger.warning(f"代理 {p['http'][:30]}... 近期成功率过低，临时禁用 {penalty}s")
                    break

    @property
    def count(self) -> int:
        return len(self._proxies)

    def get_status(self) -> list[dict]:
        result = []
        for idx, p in enumerate(self._proxies):
            h = self._health.get(idx, {})
            recent = list(h.get("recent", []))
            success_rate = (
                round(sum(1 for item in recent if item.get("ok")) / len(recent), 3)
                if recent else None
            )
            avg_latency = [
                item.get("latency", 0.0)
                for item in recent
                if item.get("ok") and item.get("latency", 0) > 0
            ]
            result.append({
                "proxy": p["http"][:40] + "...",
                "success": h.get("success", 0),
                "fail": h.get("fail", 0),
                "latency_ms": round(h.get("latency", 0) * 1000),
                "recent_success_rate": success_rate,
                "recent_avg_latency_ms": round(sum(avg_latency) / len(avg_latency) * 1000) if avg_latency else 0,
                "disabled": h.get("disabled_until", 0) > time.time()
            })
        return result

_proxy_pool = ProxyPool()

# ============ 限速器 ============

class RateLimiter:
    def __init__(self, default_rps: float = DEFAULT_RATE_LIMIT):
        self._default_rps = default_rps
        self._domain_next: dict[str, float] = {}
        self._domain_locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def _lock_for_domain(self, domain: str) -> threading.Lock:
        with self._registry_lock:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = threading.Lock()
            return self._domain_locks[domain]

    def wait(self, domain: str, rps: float | None = None):
        rate = rps or self._default_rps
        if rate <= 0:
            return
        interval = 1.0 / rate
        domain_lock = self._lock_for_domain(domain)
        while True:
            with domain_lock:
                now = time.monotonic()
                next_allowed = self._domain_next.get(domain, now)
                wait_time = next_allowed - now
                if wait_time <= 0:
                    self._domain_next[domain] = now + interval
                    return
            time.sleep(wait_time)

_rate_limiter = RateLimiter()

# ============ robots.txt 缓存 ============

_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
_robots_lock = threading.Lock()

def _check_robots(url: str) -> tuple[bool, float]:
    """检查 robots.txt，返回 (allowed, crawl_delay)"""
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    now = time.time()

    with _robots_lock:
        cached = _robots_cache.get(domain)
        if cached and now - cached[1] < 3600:
            rp = cached[0]
            delay = rp.crawl_delay("*") or 0
            return rp.can_fetch("*", url), delay

    try:
        robots_url = f"{domain}/robots.txt"
        resp = _requests_lib.get(robots_url, timeout=10, verify=VERIFY_TLS)
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        with _robots_lock:
            _robots_cache[domain] = (rp, now)
        delay = rp.crawl_delay("*") or 0
        return rp.can_fetch("*", url), delay
    except Exception:
        return True, 0

# ============ 缓存系统 ============

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
RESERVED_DB_COLUMNS = {"id", "sole_id"}

def _domain_matches(hostname: str, patterns: set[str]) -> bool:
    return _core_domain_matches(hostname, patterns)

def _host_addresses(hostname: str):
    return _core_host_addresses(hostname)

def _is_private_target(address) -> bool:
    return _core_is_private_target(address)

def _validate_url(url: str, allow_private: bool = False) -> str:
    return _core_validate_url(
        url,
        allow_private=allow_private,
        allow_private_nets=ALLOW_PRIVATE_NETS,
        allowed_domains=ALLOWED_DOMAINS,
        blocked_domains=BLOCKED_DOMAINS,
    )


def _effective_allow_private(allow_private: bool) -> bool:
    return _core_effective_allow_private(
        allow_private,
        request_override_enabled=ALLOW_REQUEST_PRIVATE_OVERRIDE,
    )


def _effective_verify_tls(verify_tls: bool) -> bool:
    return _core_effective_verify_tls(
        verify_tls,
        insecure_override_enabled=ALLOW_INSECURE_TLS_OVERRIDE,
    )

def _resolve_pinned(url: str, allow_private: bool = False):
    """v4.0: 解析后绑定 IP，防 DNS rebinding。返回 PinnedAddress 或 None。

    None 表示请求方没启用 pinning（如 hostname 已是 IP 字面量、解析失败、IPv6 等情况）。
    """
    if not PIN_DNS:
        return None
    try:
        scheme, host, port = _dns_pin_mod.parse_url_target(url)
    except ValueError:
        return None
    try:
        addresses = _dns_pin_mod.resolve_addresses(host)
    except ValueError:
        return None
    if not addresses:
        return None
    # 二次校验私有/保留地址（与 _validate_url 同源逻辑）
    if not (allow_private or ALLOW_PRIVATE_NETS):
        if any(_is_private_target(addr) for addr in addresses):
            return None  # 让上游 _validate_url 报错
    try:
        return _dns_pin_mod.build_pinned_address(url, addresses)
    except ValueError:
        return None

def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()

def _browser_storage_state_path(domain: str) -> Path:
    return COOKIE_DIR / f"browser_{CookieStore._safe_key(domain)}.json"

def _apply_request_policy(url: str, respect_robots: bool = RESPECT_ROBOTS,
                          rate_limit: float | None = None,
                          allow_private: bool = False) -> None:
    allow_private = _effective_allow_private(allow_private)
    _validate_url(url, allow_private=allow_private)
    domain = _domain_from_url(url)
    if respect_robots:
        allowed, crawl_delay = _check_robots(url)
        if not allowed:
            raise PermissionError(f"robots.txt 禁止抓取: {url}")
        if crawl_delay > 0:
            time.sleep(crawl_delay)
    _rate_limiter.wait(domain, rate_limit)

_cache_store = CacheStore(
    CACHE_DIR,
    ttl_seconds=CACHE_TTL,
    max_size_mb=CACHE_MAX_SIZE_MB,
    prune_every_writes=CACHE_PRUNE_EVERY_WRITES,
    logger=logger,
)

def _sync_cache_settings() -> None:
    _cache_store.ttl_seconds = CACHE_TTL
    _cache_store.max_size_mb = CACHE_MAX_SIZE_MB
    _cache_store.prune_every_writes = max(1, CACHE_PRUNE_EVERY_WRITES)

def _cache_variant(*parts) -> str:
    return _cache_store.variant(*parts)

def _cache_key(url: str, req_type: int = 1, variant: str = "") -> str:
    return _cache_store.key(url, req_type, variant)

def _read_cache(url: str, req_type: int = 1, variant: str = "") -> str | None:
    _sync_cache_settings()
    return _cache_store.read(url, req_type, variant)

def _prune_cache_if_needed() -> None:
    _sync_cache_settings()
    return _cache_store.prune_if_needed()

def _write_cache(url: str, text: str, req_type: int = 1, variant: str = ""):
    _sync_cache_settings()
    _cache_store.write_async(_executor, url, text, req_type, variant)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cache-writer")

# ============ 结构化事件日志 ============

EVENT_LOG_FILE = LOG_DIR / "crawler_events.jsonl"
_event_log = EventLog(EVENT_LOG_FILE, tail_lines=EVENT_LOG_TAIL_LINES, logger=logger)

def _sync_event_log_settings() -> None:
    _event_log.path = EVENT_LOG_FILE
    _event_log.tail_lines = EVENT_LOG_TAIL_LINES

def _append_event(event: dict) -> None:
    _sync_event_log_settings()
    _event_log.append(event)

def _read_recent_events(limit: int = 50, event_type: str = "", domain: str = "") -> list[dict]:
    _sync_event_log_settings()
    return _event_log.read_recent(limit, event_type, domain)

def _tail_file_lines(path: Path, max_lines: int) -> list[str]:
    return EventLog.tail_file_lines(path, max_lines, logger)

_frontier = URLFrontier(
    FRONTIER_DIR / "frontier.db",
    FRONTIER_DIR / "frontier.bloom",
    logger=logger,
    bloom_capacity=FRONTIER_BLOOM_CAPACITY,
    bloom_error_rate=FRONTIER_BLOOM_ERROR_RATE,
)
_template_store = TemplateStore(TEMPLATE_DIR)
_cookie_store = CookieStore(COOKIE_DIR)

# v4.0: 域名记忆 + 异步并发后端
_domain_memory = DomainMemory(DB_DIR / "domain_memory.db") if DOMAIN_MEMORY_ENABLED else None
_async_backend = AsyncBackend(
    timeout=REQUEST_TIMEOUT,
    max_connections=max(20, MAX_DOMAIN_SESSIONS * 2),
    verify_tls=VERIFY_TLS,
)

# ============ 轻量采集任务 ============

_job_executor = ThreadPoolExecutor(
    max_workers=_env_int("CRAWLER_JOB_WORKERS", 2),
    thread_name_prefix="crawl-job",
)
_job_lock = threading.Lock()

def _new_job_id() -> str:
    return datetime.now().strftime("job_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]

def _job_file(job_id: str) -> Path:
    if not re.match(r"^job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$", job_id):
        raise ValueError("job_id 格式非法")
    return JOB_DIR / f"{job_id}.json"

def _write_job(job: dict) -> None:
    path = _job_file(job["job_id"])
    tmp = path.with_suffix(".tmp")
    with _job_lock:
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

def _read_job(job_id: str) -> dict:
    path = _job_file(job_id)
    if not path.exists():
        raise FileNotFoundError(f"任务不存在: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))

def _safe_job_output_path(job_id: str, filename: str, suffix: str) -> Path:
    if not filename:
        filename = f"{job_id}{suffix}"
    raw_path = Path(filename)
    if raw_path.is_absolute() or raw_path.name != filename:
        raise ValueError("output_name 不能包含路径")
    safe_name = raw_path.name
    if not re.match(r"^[a-zA-Z0-9._-]+$", safe_name):
        raise ValueError("output_name 只允许字母数字.-_")
    if len(safe_name) > 200:
        raise ValueError("output_name 过长")
    return OUTPUT_DIR / safe_name

def _extract_links_from_html(html: str, source_url: str, link_selector: str,
                             base_url: str = "", max_links: int = 100) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    selector = (link_selector or "").strip()
    attr = ""
    if "@" in selector:
        selector, attr = selector.rsplit("@", 1)
        selector = selector.strip()
        attr = attr.strip()
    links = []
    for elem in soup.select(selector):
        href = elem.get(attr, "") if attr else elem.get("href", "")
        if not href:
            a_tag = elem.find("a", href=True)
            if a_tag:
                href = a_tag["href"]
        if not href:
            continue
        if not href.startswith(("http://", "https://")):
            href = urljoin(base_url or source_url, href)
        links.append({"url": href, "text": elem.get_text(strip=True)})
        if len(links) >= max_links:
            break
    return links

def _loads_relaxed_json(value: str):
    text = (value or "").strip()
    if not text:
        raise ValueError("empty JSON text")
    if text.startswith("<!--"):
        text = re.sub(r"^\s*<!--", "", text)
        text = re.sub(r"-->\s*$", "", text).strip()
    return json.loads(text)

def _path_tokens(path: str) -> list[Any]:
    tokens: list[Any] = []
    for part in re.findall(r"[^.\[\]]+|\[\d+\]|\[\*\]", path or ""):
        if part.startswith("[") and part.endswith("]"):
            inner = part[1:-1]
            tokens.append("*" if inner == "*" else int(inner))
        else:
            tokens.append(part)
    return tokens

def _extract_by_path(data, path: str):
    current = [data]
    for token in _path_tokens(path):
        next_items = []
        for item in current:
            if token == "*":
                if isinstance(item, list):
                    next_items.extend(item)
                elif isinstance(item, dict):
                    next_items.extend(item.values())
            elif isinstance(token, int):
                if isinstance(item, list) and 0 <= token < len(item):
                    next_items.append(item[token])
            else:
                if isinstance(item, dict) and token in item:
                    next_items.append(item[token])
        current = next_items
        if not current:
            return None
    if "*" in _path_tokens(path):
        return current
    return current[0] if current else None

def _value_summary(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "dict", "keys": list(value.keys())[:30], "size": len(value)}
    if isinstance(value, list):
        sample = value[0] if value else None
        sample_keys = list(sample.keys())[:20] if isinstance(sample, dict) else []
        return {"type": "list", "size": len(value), "sample_keys": sample_keys}
    return {"type": type(value).__name__, "preview": str(value)[:200]}

def _looks_like_menu(value) -> bool:
    if isinstance(value, list):
        return any(_looks_like_menu(item) for item in value[:10])
    if not isinstance(value, dict):
        return False
    keys = {str(key).lower() for key in value.keys()}
    return bool(
        keys & {"children", "childs", "items", "menu", "mainmenu", "submenus", "categories"}
        or keys & {"url", "href", "link", "path"} and keys & {"title", "name", "label", "text"}
    )

def _walk_json(value, path: str = "", max_depth: int = 8):
    yield path, value
    if max_depth <= 0:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk_json(child, child_path, max_depth - 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            yield from _walk_json(child, child_path, max_depth - 1)

def _extract_assignment_json(script_text: str, script_index: int) -> list[dict[str, Any]]:
    assignments = []
    pattern = re.compile(
        r"(?:window\.)?(?P<name>__NEXT_DATA__|__NUXT__|__INITIAL_STATE__|__PRELOADED_STATE__|__APOLLO_STATE__|INITIAL_STATE|STATE|navigation)\s*=\s*",
        re.I,
    )
    for match in pattern.finditer(script_text):
        start = match.end()
        parsed = _parse_json_like_from(script_text, start)
        if parsed is None:
            continue
        assignments.append({
            "name": match.group("name"),
            "script_index": script_index,
            "path": match.group("name"),
            "data": parsed,
        })
    return assignments

def _parse_json_like_from(text: str, start: int):
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] not in "{[":
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = ""
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = ""
            continue
        if char in {"'", '"'}:
            in_string = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                raw = text[start:index + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
    return None

def _extract_initial_state_sources(html: str, max_sources: int = 40) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    sources: list[dict[str, Any]] = []
    for index, script in enumerate(soup.find_all("script")):
        script_id = script.get("id", "")
        script_type = script.get("type", "")
        raw = script.string if script.string is not None else script.get_text("", strip=False)
        raw = html_lib.unescape(raw or "")
        if not raw.strip():
            continue
        candidates: list[dict[str, Any]] = []
        if script_id == "__NEXT_DATA__" or "json" in script_type:
            with contextlib.suppress(Exception):
                candidates.append({
                    "name": script_id or f"script[{index}]",
                    "script_index": index,
                    "path": script_id or f"script[{index}]",
                    "data": _loads_relaxed_json(raw),
                })
        candidates.extend(_extract_assignment_json(raw, index))
        for candidate in candidates:
            data = candidate["data"]
            sources.append({
                "name": candidate["name"],
                "script_index": candidate["script_index"],
                "path": candidate["path"],
                "data": data,
                "summary": _value_summary(data),
            })
            if len(sources) >= max_sources:
                return sources
    return sources

def _candidate_menu_paths(sources: list[dict[str, Any]], max_candidates: int = 30) -> list[dict[str, Any]]:
    candidates = []
    seen = set()
    for source in sources:
        for path, value in _walk_json(source["data"], max_depth=8):
            lowered = path.lower()
            if "menu" not in lowered and "navigation" not in lowered and "categor" not in lowered:
                continue
            if not _looks_like_menu(value):
                continue
            full_path = f"{source['path']}.{path}" if path else source["path"]
            if full_path in seen:
                continue
            seen.add(full_path)
            score = _score_menu_source(full_path, value)
            candidates.append({
                "path": full_path,
                "source": source["name"],
                "script_index": source["script_index"],
                "score": score,
                "confidence": round(min(score / 100, 0.99), 2),
                "summary": _value_summary(value),
            })
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:max_candidates]

def _score_menu_source(path: str, value) -> int:
    lowered = path.lower()
    score = 20
    if "multibrandmenu" in lowered:
        score += 30
    if "mainmenu" in lowered:
        score += 25
    if "navigation" in lowered:
        score += 15
    if isinstance(value, list):
        score += min(len(value), 20)
    elif isinstance(value, dict):
        score += min(len(value), 10)
    return score

def _normalize_url(url_value: str, base_url: str) -> str:
    raw = str(url_value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    return urljoin(base_url, raw) if base_url else raw

def _node_title(node: dict) -> str:
    for key in ("title", "name", "label", "text", "displayName", "categoryName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

def _node_url(node: dict, base_url: str) -> str:
    for key in ("url", "href", "link", "path", "targetUrl"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_url(value, base_url)
    link = node.get("link")
    if isinstance(link, dict):
        return _node_url(link, base_url)
    return ""

def _node_children(node: dict) -> list:
    for key in ("children", "childs", "items", "menuItems", "subMenus", "submenus", "categories", "mainMenu"):
        value = node.get(key)
        if isinstance(value, list):
            return value
    return []

def _should_filter_menu_node(node: dict, url_value: str, seen: set[tuple[str, str]]) -> str:
    title = _node_title(node)
    if node.get("hidden") is True or node.get("isHidden") is True or node.get("visible") is False:
        return "hidden"
    if node.get("contentPage") is True or str(node.get("type", "")).lower() in {"contentpage", "content_page", "cms"}:
        return "contentPage"
    if node.get("externalLink") is True or str(node.get("type", "")).lower() in {"externallink", "external_link"}:
        return "externalLink"
    key = (title.lower(), url_value)
    if title and url_value and key in seen:
        return "duplicate"
    return ""

def _menu_to_tree(menu_value, base_url: str = "", max_depth: int = 4,
                  include_filtered: bool = False) -> dict[str, Any]:
    report = {"hidden": 0, "contentPage": 0, "externalLink": 0, "duplicate": 0, "missing_title_url": 0}
    filtered_samples = []
    seen: set[tuple[str, str]] = set()

    def convert(value, depth: int) -> list[dict[str, Any]]:
        if depth > max_depth:
            return []
        items = value if isinstance(value, list) else [value]
        nodes = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _node_title(item)
            url_value = _node_url(item, base_url)
            children = _node_children(item)
            reason = _should_filter_menu_node(item, url_value, seen)
            if title and url_value and not reason:
                seen.add((title.lower(), url_value))
            if reason:
                report[reason] += 1
                if len(filtered_samples) < 20:
                    filtered_samples.append({"reason": reason, "title": title, "url": url_value})
                if include_filtered:
                    nodes.append({"title": title, "url": url_value, "filtered": True, "reason": reason})
                continue
            child_nodes = convert(children, depth + 1) if children else []
            if not title and not url_value and not child_nodes:
                report["missing_title_url"] += 1
                continue
            node = {"title": title, "url": url_value}
            if child_nodes:
                node["children"] = child_nodes
            nodes.append(node)
        return nodes

    tree = convert(menu_value, 1)
    return {
        "items": tree,
        "count": _count_tree_nodes(tree),
        "filter_report": report,
        "filtered_samples": filtered_samples,
    }

def _tree_to_title_dict(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = OrderedDict()
    for node in nodes:
        title = node.get("title") or node.get("url") or "untitled"
        children = node.get("children", [])
        if children:
            result[title] = {
                "_url": node.get("url", ""),
                "children": _tree_to_title_dict(children),
            }
        else:
            result[title] = node.get("url", "")
    return result

def _flatten_menu_urls(nodes: list[dict[str, Any]], max_urls: int = 50) -> list[str]:
    urls = []
    for node in nodes:
        url_value = node.get("url", "")
        if url_value and url_value not in urls:
            urls.append(url_value)
            if len(urls) >= max_urls:
                return urls
        for child_url in _flatten_menu_urls(node.get("children", []), max_urls=max_urls):
            if child_url not in urls:
                urls.append(child_url)
                if len(urls) >= max_urls:
                    return urls
    return urls

def _count_tree_nodes(nodes: list[dict[str, Any]]) -> int:
    total = 0
    for node in nodes:
        total += 1
        total += _count_tree_nodes(node.get("children", []))
    return total

def _scan_script_urls(html: str, base_url: str = "", max_links: int = 100) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    texts = []
    for script in soup.find_all("script"):
        texts.append(script.string if script.string is not None else script.get_text("", strip=False))
    blob = "\n".join(texts)
    url_re = re.compile(r"""(?P<url>https?://[^"'<>\s\\]+|/[A-Za-z0-9][^"'<>\s\\]*)""", re.I)
    product_hint_re = re.compile(r"(\.html$|^/p/|/p/|/product/|/products/|/catalog/|sku=|pid=)", re.I)
    links = []
    seen = set()
    for match in url_re.finditer(blob[:2_000_000]):
        href = html_lib.unescape(match.group("url").rstrip("\\,;)]}"))
        parsed_path = urlparse(href).path if href.startswith(("http://", "https://")) else href
        if not product_hint_re.search(parsed_path):
            continue
        url_value = _normalize_url(href, base_url)
        if not url_value or url_value in seen:
            continue
        seen.add(url_value)
        links.append({"url": url_value, "text": "", "source": "script_json_url"})
        if len(links) >= max_links:
            break
    return links

ACCESS_FAILURE_KEYWORDS = {
    "rate_limited": ["429", "too many requests", "rate limit", "throttle"],
    "forbidden": ["403", "forbidden", "access denied", "not authorized"],
    "login_required": ["login", "sign in", "account", "my account", "aanmelden", "inloggen"],
    "region_block": ["not available in your region", "region", "geo", "country", "blocked location"],
    "network_timeout": ["timeout", "timed out", "net::err_timed_out", "read timed out"],
    "tls_error": ["ssl", "tls", "certificate", "handshake"],
}

API_HINT_RE = re.compile(
    r"""(?P<url>https?://[^"'<>\s\\]+|/[A-Za-z0-9][^"'<>\s\\]*)""",
    re.I,
)
API_HINT_KEYWORDS = re.compile(
    r"(api|graphql|ajax|search|product|products|catalog|category|categories|page=|offset=|limit=|cursor=|sort=|filter=)",
    re.I,
)
NETWORK_DATA_KEYWORDS = re.compile(
    r"(api|graphql|ajax|search|complete|product|products|catalog|category|categories|browse|"
    r"page=|offset=|limit=|cursor=|sort=|filter=|qid=|node=)",
    re.I,
)
PAGINATION_PARAM_NAMES = {"page", "p", "offset", "limit", "cursor", "start", "from", "size", "sort", "filter", "node"}
NON_PAGINATION_PARAM_NAMES = {"pagetype", "currentpagetype", "currentsubpagetype", "hostpagetype", "hostsubpagetype"}
STRONG_PAGINATION_PARAM_NAMES = {"page", "p", "offset", "cursor", "start", "from"}

def _is_json_like_url(url_value: str) -> bool:
    path = urlparse(url_value).path.lower()
    return path.endswith((".json", ".graphql")) or "/graphql" in path or "/api/" in path

def _looks_like_static_asset_url(url_value: str) -> bool:
    parsed = urlparse(url_value)
    path = parsed.path.lower()
    return bool(re.search(r"\.(?:js|css|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|map)$", path))

def _scan_api_hints(html: str, base_url: str = "", max_items: int = 80) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    blobs: list[tuple[str, str]] = []
    for script in soup.find_all("script"):
        source = "script"
        if script.get("src"):
            blobs.append(("script_src", str(script.get("src"))))
        text = script.string if script.string is not None else script.get_text("", strip=False)
        if text:
            blobs.append((source, text))
    for elem in soup.find_all(["a", "link"], href=True):
        blobs.append((elem.name, str(elem.get("href"))))

    seen = set()
    hints = []
    for source, blob in blobs:
        for match in API_HINT_RE.finditer((blob or "")[:2_000_000]):
            raw = html_lib.unescape(match.group("url").rstrip("\\,;)]}"))
            if not API_HINT_KEYWORDS.search(raw):
                continue
            url_value = _normalize_url(raw, base_url)
            if not url_value or url_value in seen:
                continue
            if _looks_like_static_asset_url(url_value) and not _is_json_like_url(url_value):
                continue
            seen.add(url_value)
            parsed = urlparse(url_value)
            hints.append({
                "url": url_value,
                "source": source,
                "host": parsed.netloc,
                "path": parsed.path,
                "json_like": _is_json_like_url(url_value),
                "pagination_hint": bool(re.search(r"(page=|offset=|limit=|cursor=|p=)", url_value, re.I)),
            })
            if len(hints) >= max_items:
                return hints
    return hints

def _network_entry_score(entry: dict[str, Any]) -> int:
    score = 0
    url_value = entry.get("url", "")
    lowered_type = str(entry.get("resource_type", "")).lower()
    content_type = str(entry.get("content_type", "")).lower()
    if lowered_type in {"xhr", "fetch"}:
        score += 35
    if "json" in content_type or entry.get("json_like"):
        score += 25
    if NETWORK_DATA_KEYWORDS.search(url_value):
        score += 20
    if _has_strong_pagination_params(entry.get("pagination_params") or {}):
        score += 20
    status = int(entry.get("status") or 0)
    if 200 <= status < 300:
        score += 10
    if _looks_like_static_asset_url(url_value) and not entry.get("json_like"):
        score -= 50
    return score

def _pagination_params_from_url(url_value: str) -> dict[str, str]:
    parsed = urlparse(url_value or "")
    params = OrderedDict()
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in NON_PAGINATION_PARAM_NAMES:
            continue
        if lowered in PAGINATION_PARAM_NAMES or re.search(r"(page|offset|limit|cursor|sort|filter|node)", lowered):
            params[key] = value
    return dict(params)

def _has_strong_pagination_params(params: dict[str, str]) -> bool:
    return any(str(key).lower() in STRONG_PAGINATION_PARAM_NAMES for key in (params or {}))

def _summarize_network_entries(entries: list[dict[str, Any]], max_candidates: int = 30) -> dict[str, Any]:
    cleaned = []
    seen = set()
    for entry in entries:
        url_value = entry.get("url", "")
        if not url_value or url_value in seen:
            continue
        seen.add(url_value)
        item = dict(entry)
        item.setdefault("pagination_params", _pagination_params_from_url(url_value))
        item["json_like"] = bool(item.get("json_like") or _is_json_like_url(url_value) or "json" in str(item.get("content_type", "")).lower())
        item["static_asset"] = _looks_like_static_asset_url(url_value) and not item["json_like"]
        item["score"] = _network_entry_score(item)
        cleaned.append(item)
    candidates = sorted(cleaned, key=lambda item: item.get("score", 0), reverse=True)
    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in cleaned:
        status_counts[str(item.get("status", "unknown"))] = status_counts.get(str(item.get("status", "unknown")), 0) + 1
        type_counts[str(item.get("resource_type", "unknown"))] = type_counts.get(str(item.get("resource_type", "unknown")), 0) + 1
    return {
        "total": len(cleaned),
        "status_counts": status_counts,
        "resource_type_counts": type_counts,
        "candidate_count": len([item for item in candidates if item.get("score", 0) > 0]),
        "candidates": candidates[:max(1, min(int(max_candidates), 100))],
    }

def _network_recommendations(summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = summary.get("candidates", [])
    recommendations: list[dict[str, Any]] = []
    if candidates:
        best = candidates[0]
        recommendations.append({
            "type": "network_api_candidate",
            "url": best.get("url"),
            "method": best.get("method", "GET"),
            "resource_type": best.get("resource_type"),
            "score": best.get("score"),
            "reason": "浏览器网络观测发现高分数据请求候选，Agent 可优先复核它是否承载公开列表/详情数据。",
        })
    paged = [item for item in candidates if _has_strong_pagination_params(item.get("pagination_params") or {})]
    if paged:
        recommendations.append({
            "type": "pagination_candidate",
            "url": paged[0].get("url"),
            "params": paged[0].get("pagination_params"),
            "reason": "URL 中存在翻页/筛选参数，可用于推断分页策略。",
        })
    json_candidates = [item for item in candidates if item.get("json_like")]
    if json_candidates:
        recommendations.append({
            "type": "json_api_candidate",
            "url": json_candidates[0].get("url"),
            "reason": "候选请求看起来像 JSON/API，适合进一步用 fetch_json 或采集框架验证。",
        })
    if not recommendations:
        recommendations.append({
            "type": "dom_extraction",
            "reason": "未观察到强 API 候选，建议从渲染后的 DOM selector 入手。",
        })
    return recommendations

def _same_site_url(url_value: str, base_url: str) -> bool:
    parsed = urlparse(url_value or "")
    base = urlparse(base_url or "")
    return parsed.netloc.lower() == base.netloc.lower() or not parsed.netloc

def _looks_like_detail_url(url_value: str) -> bool:
    path = urlparse(url_value or "").path
    return bool(re.search(r"(/dp/[A-Z0-9]{10}\b|/gp/product/|/detail/|/product/|/products/|/p/|\.html$)", path, re.I))

def _pagination_candidates_from_html(html: str, base_url: str, max_candidates: int = 30) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[dict[str, Any]] = []
    seen = set()
    current = urlparse(base_url)
    current_params = dict(parse_qsl(current.query, keep_blank_values=True))

    def add(kind: str, href: str, text: str = "", confidence: float = 0.5, evidence: str = ""):
        url_value = _normalize_url(href, base_url)
        if not url_value or url_value in seen or not _same_site_url(url_value, base_url):
            return
        seen.add(url_value)
        parsed = urlparse(url_value)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        changed = {
            key: value for key, value in params.items()
            if current_params.get(key) != value
        }
        page_params = {
            key: value for key, value in changed.items()
            if key.lower() in PAGINATION_PARAM_NAMES or re.search(r"(page|offset|cursor|start|from)", key, re.I)
        }
        candidates.append({
            "type": kind,
            "url": url_value,
            "text": clean_text_for_output(text),
            "confidence": confidence,
            "pagination_params": page_params or _pagination_params_from_url(url_value),
            "same_path": parsed.path == current.path,
            "evidence": evidence,
        })

    for link in soup.select('a[rel~="next"], link[rel~="next"]'):
        href = link.get("href", "")
        add("rel_next", href, link.get_text(" ", strip=True), 0.95, "rel=next")

    next_patterns = re.compile(r"^(next|next page|›|»|volgende|suivant|weiter|siguiente)$", re.I)
    for a in soup.find_all("a", href=True):
        text = clean_text_for_output(a.get_text(" ", strip=True) or a.get("aria-label", "") or a.get("title", ""))
        href = a.get("href", "")
        if next_patterns.search(text):
            add("next_link", href, text, 0.85, "next text/label")
            continue
        parsed = urlparse(urljoin(base_url, href))
        if parsed.path != current.path:
            continue
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            if key.lower() in PAGINATION_PARAM_NAMES and value.isdigit():
                add("query_page", href, text, 0.72, f"query param {key}={value}")
                break

    # Detect URL pattern from current URL even if the page omits a visible next link.
    for key, value in current_params.items():
        if key.lower() in PAGINATION_PARAM_NAMES and (value.isdigit() or key.lower() in {"page", "p"}):
            try:
                next_value = str(int(value or "1") + 1)
            except ValueError:
                next_value = "2"
            new_params = current_params.copy()
            new_params[key] = next_value
            query = "&".join(f"{k}={v}" for k, v in new_params.items())
            add("current_query_pattern", current._replace(query=query).geturl(), key, 0.78, f"current query param {key}")
    if "page" not in {key.lower() for key in current_params}:
        separator = "&" if current.query else "?"
        add("page_param_guess", base_url + separator + "page=2", "page=2", 0.45, "common page parameter guess")

    return sorted(candidates, key=lambda item: item.get("confidence", 0), reverse=True)[:max_candidates]

def clean_text_for_output(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()

def _sample_next_urls_from_strategy(url: str, strategy: dict[str, Any], max_pages: int) -> list[str]:
    if not strategy:
        return []
    first = strategy.get("url", "")
    parsed_first = urlparse(first)
    params = dict(parse_qsl(parsed_first.query, keep_blank_values=True))
    page_keys = [key for key in params if key.lower() in PAGINATION_PARAM_NAMES and str(params[key]).isdigit()]
    urls = []
    if page_keys:
        key = page_keys[0]
        start_num = int(params[key])
        for page_num in range(start_num, start_num + max(1, min(int(max_pages), 10))):
            new_params = params.copy()
            new_params[key] = str(page_num)
            query = "&".join(f"{k}={v}" for k, v in new_params.items())
            urls.append(parsed_first._replace(query=query).geturl())
    elif first:
        urls.append(first)
    return list(OrderedDict((item, None) for item in urls).keys())

def _fetch_rendered_or_static(url: str, mode: str, use_cache: bool = True,
                              wait_selector: str = "", render_time: float = 5.0,
                              wait_until: str = "domcontentloaded",
                              scroll_count: int = 0, scroll_delay: float = 1.0,
                              respect_robots: bool = RESPECT_ROBOTS,
                              allow_private: bool = False) -> str:
    if mode == "browser":
        return _engine.fetch_with_browser(
            url,
            wait_until=wait_until,
            render_time=render_time,
            wait_selector=wait_selector,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
        )
    return _smart_fetch(
        url,
        mode=mode,
        use_cache=use_cache,
        respect_robots=respect_robots,
        allow_private=allow_private,
    )

def _extract_detail_links_from_list(html: str, list_url: str, list_selector: str = "", max_links: int = 50) -> tuple[list[dict[str, str]], str]:
    selector = list_selector
    if not selector:
        inferred = _infer_selector_candidates(
            html,
            base_url=list_url,
            target_fields=["list_link"],
            max_candidates=8,
        )
        selector = _first_field_candidate(inferred, "list_link").get("selector", "")
    links = _extract_links_from_html(html, list_url, selector, list_url, max_links=max_links * 3) if selector else []
    if not links:
        links = _extract_links_from_html(html, list_url, "a@href", list_url, max_links=max_links * 5)
    filtered = []
    seen = set()
    for link in links:
        url_value = link.get("url", "")
        if not url_value or url_value in seen or not _same_site_url(url_value, list_url):
            continue
        if not _looks_like_detail_url(url_value):
            continue
        seen.add(url_value)
        filtered.append(link)
        if len(filtered) >= max_links:
            break
    return filtered, selector

def _extract_fields_with_selectors(html: str, fields: dict[str, str]) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    record: dict[str, Any] = {}
    for name, selector in (fields or {}).items():
        if not selector:
            record[name] = ""
            continue
        if "@" in selector:
            css, attr = selector.rsplit("@", 1)
            elem = soup.select_one(css.strip())
            record[name] = elem.get(attr.strip(), "") if elem else ""
        else:
            elems = soup.select(selector)
            values = [clean_text_for_output(elem.get_text(" ", strip=True)) for elem in elems[:5]]
            record[name] = values[0] if len(values) == 1 else values
    return record

def _detail_field_risk_flags(detail_spec: dict[str, str], samples: list[dict[str, Any]]) -> list[str]:
    flags = []
    body_selector = (detail_spec or {}).get("body", "")
    if body_selector and re.search(r"(twister|variant|color|size|swatch|option)", body_selector, re.I):
        flags.append("body_selector_may_point_to_variant_options")
    price_selector = (detail_spec or {}).get("price", "")
    if price_selector and re.search(r"(update|buybox|payment|subtotal)", price_selector, re.I):
        flags.append("price_selector_may_include_buybox_payment_text")
    for sample in samples[:5]:
        values = sample.get("values", {})
        body = values.get("body", "")
        body_text = " ".join(body) if isinstance(body, list) else str(body or "")
        if len(body_text) > 800 and re.search(r"(In Stock|Make a .* selection|See .* options)", body_text, re.I):
            flags.append("body_value_contains_option_or_offer_noise")
            break
    return list(OrderedDict((flag, None) for flag in flags).keys())

def _classify_access_result(html: str = "", error: str = "", status: int | None = None) -> dict[str, Any]:
    visible_text = ""
    if html:
        with contextlib.suppress(Exception):
            visible_text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)[:20000]
    text = f"{error}\n{visible_text}".lower()
    challenge = _detect_challenge_page(html or "") if html else ""
    categories = []
    if challenge or any(pattern.lower() in text for pattern in CHALLENGE_PATTERNS):
        categories.append("challenge")
    if status:
        if status == 429:
            categories.append("rate_limited")
        elif status in {401, 403}:
            categories.append("forbidden")
        elif status >= 500:
            categories.append("server_error")
    for category, keywords in ACCESS_FAILURE_KEYWORDS.items():
        if category == "login_required" and len(visible_text) > 2000:
            continue
        if any(keyword in text for keyword in keywords) and category not in categories:
            categories.append(category)
    if html and len(html) >= max(0, FETCH_MAX_LENGTH - 16):
        categories.append("html_truncated")
    if html:
        diag = _diagnose_html(html)
        if "js_rendering_likely_required" in diag.get("findings", []):
            categories.append("js_shell")
        if _scan_api_hints(html, max_items=1):
            categories.append("api_hints_found")
    if not categories and error:
        categories.append("fetch_error")
    if not categories:
        categories.append("html_available")
    return {
        "categories": list(OrderedDict.fromkeys(categories)),
        "challenge": challenge,
        "status": status,
    }

def _extract_http_status(error: str) -> int | None:
    match = re.search(r"\bHTTP\s+(\d{3})\b|(\d{3})\s+(?:Client|Server) Error", error or "", re.I)
    if not match:
        return None
    return int(match.group(1) or match.group(2))

def _access_probe_recommendations(probes: list[dict[str, Any]], proxy: dict[str, Any],
                                  api_hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    successful = [probe for probe in probes if probe.get("ok")]
    challenge = [probe for probe in probes if "challenge" in probe.get("classification", {}).get("categories", [])]
    js_shell = [probe for probe in successful if "js_shell" in probe.get("classification", {}).get("categories", [])]
    truncated = [probe for probe in successful if "html_truncated" in probe.get("classification", {}).get("categories", [])]
    if successful:
        best = max(successful, key=lambda item: (item.get("text_chars", 0), item.get("html_bytes", 0)))
        recommendations.append({
            "type": "preferred_fetch_mode",
            "mode": best.get("mode"),
            "use_proxy": best.get("use_proxy", False),
            "reason": "该路径成功返回可分析 HTML，文本量/HTML 体积在探测结果中最好。",
        })
    if js_shell:
        recommendations.append({
            "type": "browser_rendering",
            "action": {"mode": "browser", "wait_until": "networkidle", "render_time": 5, "scroll_count": 2},
            "reason": "页面像客户端渲染壳，Agent 写采集代码时应优先等待渲染完成或改用公开 JSON/API 数据。",
        })
    if truncated:
        recommendations.append({
            "type": "increase_fetch_limit",
            "action": {"FETCH_MAX_LENGTH": max(FETCH_MAX_LENGTH * 5, 500000)},
            "reason": "HTML 命中当前截断上限，结构化数据或商品 URL 可能在截断之后。",
        })
    if api_hints:
        recommendations.append({
            "type": "api_discovery",
            "sample_count": len(api_hints),
            "action": "优先人工/Agent 复核这些公开接口线索，再决定是否用 fetch_json 或采集框架直连接口。",
            "reason": "页面脚本中出现 API/商品/目录/翻页相关 URL，通常比 DOM selector 更适合固定采集格式。",
        })
    if challenge:
        recommendations.append({
            "type": "authorized_session_or_manual_review",
            "reason": "至少一种访问路径返回 challenge/captcha。不要自动破解；建议使用授权 Cookie profile、公开 API、降低频率或人工确认站点许可。",
        })
    if proxy.get("local_proxy"):
        recommendations.append({
            "type": "proxy_available",
            "proxy": proxy["local_proxy"],
            "reason": "已设置本地代理，可在探测计划中对比直连和代理路径。",
        })
    elif proxy.get("suggested_local_proxy"):
        recommendations.append({
            "type": "proxy_optional",
            "proxy": proxy["suggested_local_proxy"],
            "reason": "检测到常见本地代理端口配置；需要时可先调用 set_proxy 后再启用 use_proxy。",
        })
    if not recommendations:
        recommendations.append({
            "type": "standard_strategy",
            "action": {"mode": "auto", "use_cache": True},
            "reason": "未发现强反爬或强 JS 壳信号。",
        })
    return recommendations

def _probe_access_modes(url: str, target_selector: str = "", modes: list[str] | None = None,
                        use_proxy: bool = False, include_browser: bool = True,
                        use_cache: bool = False, respect_robots: bool = RESPECT_ROBOTS,
                        allow_private: bool = False,
                        wait_selector: str = "", render_time: float = 5.0,
                        wait_until: str = "domcontentloaded",
                        scroll_count: int = 0, scroll_delay: float = 1.0) -> dict[str, Any]:
    modes = modes or ["requests", "curl_cffi", "browser"]
    if not include_browser:
        modes = [mode for mode in modes if mode != "browser"]
    _apply_request_policy(url, respect_robots=respect_robots, rate_limit=None,
                          allow_private=allow_private)
    probes = []
    best_html = ""
    for mode in modes:
        if mode == "curl_cffi" and not HAS_CURL_CFFI:
            probes.append({"mode": mode, "use_proxy": use_proxy, "ok": False, "skipped": True, "reason": "curl_cffi_not_installed"})
            continue
        if mode == "browser" and not HAS_PLAYWRIGHT:
            probes.append({"mode": mode, "use_proxy": use_proxy, "ok": False, "skipped": True, "reason": "playwright_not_installed"})
            continue
        started = time.time()
        try:
            if mode == "browser":
                html = _engine.fetch_with_browser(
                    url,
                    wait_until=wait_until,
                    render_time=render_time,
                    wait_selector=wait_selector,
                    scroll_count=scroll_count,
                    scroll_delay=scroll_delay,
                )
            else:
                html = _smart_fetch(
                    url,
                    mode=mode,
                    use_cache=use_cache,
                    use_proxy=use_proxy,
                    respect_robots=respect_robots,
                    allow_private=allow_private,
                    save_cache=False,
                )
            soup = BeautifulSoup(html or "", "html.parser")
            target_count = len(soup.select(target_selector)) if target_selector else 0
            classification = _classify_access_result(html=html)
            probe = {
                "mode": mode,
                "use_proxy": use_proxy,
                "ok": "challenge" not in classification["categories"],
                "html_bytes": len(html or ""),
                "text_chars": len(soup.get_text(" ", strip=True)),
                "script_count": len(soup.find_all("script")),
                "dom_anchor_count": len(soup.find_all("a", href=True)),
                "target_count": target_count,
                "classification": classification,
                "duration_ms": round((time.time() - started) * 1000),
            }
            probes.append(probe)
            if len(html or "") > len(best_html):
                best_html = html or ""
        except Exception as exc:
            error = str(exc)
            probes.append({
                "mode": mode,
                "use_proxy": use_proxy,
                "ok": False,
                "error_type": type(exc).__name__,
                "error": error[:500],
                "classification": _classify_access_result(error=error, status=_extract_http_status(error)),
                "duration_ms": round((time.time() - started) * 1000),
            })
    api_hints = _scan_api_hints(best_html, base_url=url, max_items=80) if best_html else []
    local_proxy = getattr(_proxy_pool, "_local_proxy", "") or ""
    proxy_info = {
        "local_proxy": local_proxy,
        "pool_count": _proxy_pool.count,
        "pool_status": _proxy_pool.get_status() if _proxy_pool.count > 0 else [],
        "suggested_local_proxy": "http://127.0.0.1:7890",
        "note": "如需使用本地代理，请用 set_proxy 设置你的 HTTP 代理端口；端口以本机代理客户端为准。",
    }
    summary_categories = sorted({
        category
        for probe in probes
        for category in probe.get("classification", {}).get("categories", [])
    })
    return {
        "url": url,
        "probes": probes,
        "summary": {
            "ok": any(probe.get("ok") for probe in probes),
            "categories": summary_categories,
            "best_mode": next((probe["mode"] for probe in probes if probe.get("ok")), ""),
            "api_hint_count": len(api_hints),
            "target_selector": target_selector,
        },
        "api_hints": api_hints[:40],
        "proxy": proxy_info,
        "recommendations": _access_probe_recommendations(probes, proxy_info, api_hints),
    }

def _resolve_initial_state_path(sources: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    wanted = (path or "").strip()
    if not wanted:
        return None
    first = wanted.split(".", 1)[0].split("[", 1)[0]
    for source in sources:
        candidates = [wanted]
        for prefix in {source.get("path", ""), source.get("name", ""), first}:
            if prefix and wanted == prefix:
                candidates.append("")
            elif prefix and wanted.startswith(prefix + "."):
                candidates.append(wanted[len(prefix) + 1:])
        for candidate_path in candidates:
            if candidate_path == "":
                value = source["data"]
            else:
                value = _extract_by_path(source["data"], candidate_path)
            if value is not None:
                return {
                    "path": wanted,
                    "source": source["name"],
                    "script_index": source["script_index"],
                    "value": value,
                    "resolved_relative_path": candidate_path,
                }
    return None

def _diagnose_zero_link_result(html: str, source_url: str, selector: str,
                               base_url: str, max_links: int) -> dict[str, Any]:
    diag = _diagnose_html(html or "", url=source_url, target_selector=selector)
    soup = BeautifulSoup(html or "", "html.parser")
    all_dom_links = len(soup.find_all("a", href=True))
    fallback_links = _scan_script_urls(html or "", base_url or source_url, max_links=max_links)
    truncated_likely = len(html or "") >= max(0, FETCH_MAX_LENGTH - 16)
    return {
        "reason": "selector_zero_match",
        "selector": selector,
        "html_bytes": len(html or ""),
        "fetch_max_length": FETCH_MAX_LENGTH,
        "truncated_likely": truncated_likely,
        "dom_anchor_count": all_dom_links,
        "script_url_count": len(fallback_links),
        "script_url_samples": fallback_links[:20],
        "challenge": diag.get("signals", {}).get("challenge", ""),
        "findings": diag.get("findings", []),
        "recommendations": diag.get("recommendations", []),
    }

def _first_field_candidate(selector_result: dict[str, Any], field: str) -> dict[str, Any]:
    candidates = selector_result.get("fields", {}).get(field, [])
    return candidates[0] if candidates else {}

def _make_recommendations(access: dict[str, Any], menu_candidates: list[dict[str, Any]],
                          selector_result: dict[str, Any], script_links: list[dict[str, str]],
                          dom_link_count: int) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if access.get("signals", {}).get("challenge"):
        recommendations.append({
            "type": "manual_access_required",
            "reason": "页面疑似 challenge 或验证码，采集前需要合法登录态/API/授权访问。",
        })
    if "js_rendering_likely_required" in access.get("findings", []):
        recommendations.append({
            "type": "fetch_strategy",
            "mode": "browser",
            "reason": "页面像 JS shell，建议使用 browser 渲染、等待关键元素并适当滚动。",
        })
    if menu_candidates:
        recommendations.append({
            "type": "menu_source",
            "path": menu_candidates[0]["path"],
            "confidence": menu_candidates[0]["confidence"],
            "reason": "发现前端初始状态中的菜单候选，可优先用于目录重建。",
        })
    list_candidate = _first_field_candidate(selector_result, "list_link")
    if list_candidate:
        recommendations.append({
            "type": "list_selector",
            "selector": list_candidate["selector"],
            "count": list_candidate["count"],
            "reason": list_candidate.get("reason", "ranked_selector_candidate"),
        })
    elif script_links:
        recommendations.append({
            "type": "script_url_fallback",
            "count": len(script_links),
            "reason": "DOM 列表 selector 不明显，但脚本数据里存在商品/详情 URL。",
        })
    elif dom_link_count:
        recommendations.append({
            "type": "selector_review",
            "count": dom_link_count,
            "reason": "DOM 中有链接，但没有高置信商品列表 selector，建议人工或视觉 Agent 复核。",
        })
    if not recommendations:
        recommendations.append({
            "type": "needs_review",
            "reason": "未发现强菜单、商品链接或结构化候选，建议提供截图或使用 browser 渲染后重试。",
        })
    return recommendations

def _merge_recommendations(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    merged = []
    for group in groups:
        for item in group or []:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged

def _build_collection_pipeline(plan: dict[str, Any], validate_only: bool = False) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("plan 必须是 JSON 对象")
    start_url = plan.get("start_url") or plan.get("url") or plan.get("list_url")
    if not start_url:
        raise ValueError("plan 需要 start_url/url/list_url")
    mode = plan.get("mode", "auto")
    max_items = max(1, min(int(plan.get("max_items", 20)), 1000))
    fields = plan.get("fields") or plan.get("detail_fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("plan.fields 必须是对象")

    pipeline = {
        "mode": mode,
        "use_cache": bool(plan.get("use_cache", True)),
        "output_format": plan.get("output_format", "records"),
        "steps": [],
    }
    list_selector = plan.get("list_selector") or plan.get("selector") or plan.get("item_link")
    category_urls = plan.get("category_urls") or []
    detail_urls = plan.get("detail_urls") or plan.get("urls") or []
    if list_selector and category_urls:
        pipeline["steps"].append({
            "step": "crawl_lists",
            "urls": category_urls,
            "selector": list_selector,
            "base_url": plan.get("base_url", start_url),
            "max_items": max_items,
            "respect_robots": bool(plan.get("respect_robots", RESPECT_ROBOTS)),
        })
    elif list_selector:
        pipeline["steps"].append({
            "step": "crawl_list",
            "url": start_url,
            "selector": list_selector,
            "base_url": plan.get("base_url", start_url),
            "max_items": max_items,
            "respect_robots": bool(plan.get("respect_robots", RESPECT_ROBOTS)),
        })
    elif detail_urls:
        pipeline["steps"].append({
            "step": "frontier_add",
            "urls": detail_urls,
            "priority": 10,
            "kind": "detail",
        })
        pipeline["steps"].append({"step": "frontier_next", "limit": max_items, "worker_id": "collection_plan"})
    else:
        pipeline["steps"].append({
            "step": "frontier_add",
            "urls": [start_url],
            "priority": 10,
            "kind": "detail",
        })
        pipeline["steps"].append({"step": "frontier_next", "limit": 1, "worker_id": "collection_plan"})

    if fields:
        pipeline["steps"].append({"step": "crawl_products", "fields": fields, "max_items": max_items})
    if plan.get("filter"):
        pipeline["steps"].append({"step": "filter", "condition": str(plan["filter"])})
    output = plan.get("output", "json")
    if output in {"db", "both"}:
        pipeline["steps"].append({
            "step": "save",
            "db": plan.get("db_name", "crawler_data"),
            "table": plan.get("table", "items"),
        })
    if output in {"json", "both"} and not validate_only:
        pipeline["steps"].append({
            "step": "save_json",
            "filename": plan.get("output_name", f"collection_{int(time.time())}.json"),
            "format": plan.get("output_format", "records"),
        })
    return pipeline

def _format_output_records(records: list[dict[str, Any]], output_format: str = "records") -> Any:
    fmt = (output_format or "records").strip().lower()
    if fmt in {"records", "list", "raw"}:
        return records
    if fmt in {"dict", "url_dict", "title_url"}:
        result: dict[str, Any] = OrderedDict()
        for index, record in enumerate(records, start=1):
            title = str(record.get("title") or record.get("name") or record.get("text") or f"item_{index}").strip()
            result[title] = record.get("url", record)
        return result
    if fmt in {"tree", "by_source"}:
        grouped: dict[str, list[dict[str, Any]]] = OrderedDict()
        for record in records:
            source = str(record.get("source_list_url") or record.get("category") or "items")
            grouped.setdefault(source, []).append(record)
        return grouped
    raise ValueError("output_format 只支持 records/list/dict/url_dict/tree/by_source")

def _plan_risk_flags(plan: dict[str, Any], scout: dict[str, Any] | None = None) -> list[str]:
    flags = []
    if not (plan.get("list_selector") or plan.get("detail_urls") or plan.get("urls")):
        flags.append("single_detail_fallback")
    if plan.get("category_urls") and not plan.get("menu_source_path"):
        flags.append("category_urls_without_menu_source")
    if plan.get("mode") == "browser":
        flags.append("browser_rendering_required")
    if scout:
        if scout.get("access", {}).get("signals", {}).get("challenge"):
            flags.append("challenge_detected")
        if scout.get("page", {}).get("truncated_likely"):
            flags.append("html_truncated_likely")
        if plan.get("detail_urls") and not plan.get("list_selector"):
            flags.append("script_url_fallback")
    missing_fields = [field for field, selector in (plan.get("fields") or {}).items() if not selector]
    if missing_fields:
        flags.append("missing_field_selectors:" + ",".join(missing_fields))
    return flags

def _add_plan_metadata(plan: dict[str, Any], scout: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = dict(plan)
    plan.setdefault("version", SERVER_PROTOCOL_VERSION)
    plan.setdefault("kind", "collection_plan")
    plan.setdefault("assumptions", [])
    if plan.get("list_selector"):
        plan["assumptions"].append("list_selector points to detail-page URLs")
    if plan.get("detail_urls"):
        plan["assumptions"].append("detail_urls are crawlable product/detail pages")
    if plan.get("category_urls"):
        plan["assumptions"].append("category_urls are list pages that share list_selector")
    if plan.get("menu_source_path"):
        plan["assumptions"].append("menu_source_path identifies the category/menu source used to derive category_urls")
    if plan.get("fields"):
        plan["assumptions"].append("field selectors are evaluated on detail pages")
    plan["risk_flags"] = _plan_risk_flags(plan, scout=scout)
    return plan

def _validate_collection_plan_shape(plan: dict[str, Any]) -> dict[str, Any]:
    errors = []
    warnings = []
    start_url = plan.get("start_url") or plan.get("url") or plan.get("list_url")
    if not start_url:
        errors.append("missing_start_url")
    else:
        try:
            _validate_url(start_url, allow_private=_effective_allow_private(bool(plan.get("allow_private", False))))
        except Exception as exc:
            errors.append(f"invalid_start_url:{exc}")
    if not (plan.get("list_selector") or plan.get("selector") or plan.get("item_link") or plan.get("detail_urls") or plan.get("urls")):
        warnings.append("no_list_selector_or_detail_urls; plan will treat start_url as a single detail page")
    category_urls = plan.get("category_urls") or []
    if category_urls and not isinstance(category_urls, list):
        errors.append("category_urls_must_be_array")
    for item_url in category_urls[:100]:
        try:
            _validate_url(str(item_url), allow_private=_effective_allow_private(bool(plan.get("allow_private", False))))
        except Exception as exc:
            errors.append(f"invalid_category_url:{exc}")
            break
    fields = plan.get("fields") or plan.get("detail_fields") or {}
    if fields and not isinstance(fields, dict):
        errors.append("fields_must_be_object")
    if not fields:
        warnings.append("no_fields; execution will only collect links/frontier entries")
    return {"ok": not errors, "errors": errors, "warnings": warnings}

def _scout_page_data(url: str, goal: str = "product_list", mode: str = "auto",
                     use_cache: bool = True, target_selector: str = "",
                     max_candidates: int = 8,
                     respect_robots: bool = RESPECT_ROBOTS,
                     allow_private: bool = False) -> dict[str, Any]:
    html = _smart_fetch(
        url,
        mode=mode,
        use_cache=use_cache,
        respect_robots=respect_robots,
        allow_private=allow_private,
    )
    soup = BeautifulSoup(html or "", "html.parser")
    access = _diagnose_html(html or "", url=url, target_selector=target_selector)
    sources = _extract_initial_state_sources(html)
    menu_candidates = _candidate_menu_paths(sources, max_candidates=max_candidates)
    selector_result = _infer_selector_candidates(
        html,
        base_url=url,
        target_fields=["list_link", "title", "price", "image_src", "body"],
        max_candidates=max(1, min(int(max_candidates), 20)),
    )
    script_links = _scan_script_urls(html, base_url=url, max_links=100)
    api_hints = _scan_api_hints(html, base_url=url, max_items=80)
    dom_links = _extract_links_from_html(html, url, "a@href", url, max_links=100)
    recommended_plan = {
        "version": SERVER_PROTOCOL_VERSION,
        "kind": "collection_plan",
        "start_url": url,
        "mode": "browser" if "js_rendering_likely_required" in access.get("findings", []) else mode,
        "base_url": url,
        "max_items": 20,
        "list_selector": _first_field_candidate(selector_result, "list_link").get("selector", ""),
        "fields": selector_result.get("best_spec_fragment", {}).get("detail", {}),
        "output": "json",
    }
    if not recommended_plan["list_selector"] and script_links:
        recommended_plan["detail_urls"] = [item["url"] for item in script_links[:20]]
    if api_hints and not recommended_plan["list_selector"]:
        recommended_plan.setdefault("strategy_notes", []).append(
            "页面存在 API/翻页/商品接口线索，建议 Agent 优先复核 api_hints 后再写采集代码。"
        )
    recommended_plan = _add_plan_metadata(recommended_plan, scout={
        "access": access,
        "page": {
            "truncated_likely": len(html or "") >= max(0, FETCH_MAX_LENGTH - 16),
        },
    })
    return {
        "ok": not bool(access.get("signals", {}).get("challenge")),
        "url": url,
        "goal": goal,
        "mode_used": mode,
        "page": {
            "html_bytes": len(html or ""),
            "text_chars": len(soup.get_text(" ", strip=True)),
            "dom_anchor_count": len(soup.find_all("a", href=True)),
            "script_count": len(soup.find_all("script")),
            "truncated_likely": len(html or "") >= max(0, FETCH_MAX_LENGTH - 16),
        },
        "access": access,
        "initial_state": {
            "source_count": len(sources),
            "sources": [
                {
                    "name": source["name"],
                    "script_index": source["script_index"],
                    "path": source["path"],
                    "summary": source["summary"],
                }
                for source in sources[:20]
            ],
        },
        "menu_candidates": menu_candidates,
        "link_candidates": selector_result.get("fields", {}).get("list_link", []),
        "field_candidates": {
            key: value for key, value in selector_result.get("fields", {}).items()
            if key != "list_link"
        },
        "script_url_candidates": {
            "count": len(script_links),
            "sample": script_links[:20],
        },
        "api_hints": {
            "count": len(api_hints),
            "sample": api_hints[:20],
        },
        "_initial_state_sources": sources,
        "dom_link_sample": dom_links[:20],
        "recommended_plan": recommended_plan,
        "recommendations": _merge_recommendations(
            _make_recommendations(
                access,
                menu_candidates,
                selector_result,
                script_links,
                len(soup.find_all("a", href=True)),
            ),
            _access_probe_recommendations(
                [{
                    "mode": mode,
                    "use_proxy": False,
                    "ok": not bool(access.get("signals", {}).get("challenge")),
                    "html_bytes": len(html or ""),
                    "text_chars": len(soup.get_text(" ", strip=True)),
                    "classification": _classify_access_result(html=html),
                }],
                {
                    "local_proxy": getattr(_proxy_pool, "_local_proxy", "") or "",
                    "pool_count": _proxy_pool.count,
                    "suggested_local_proxy": "http://127.0.0.1:8800",
                },
                api_hints,
            ),
        ),
    }

def _requested_fields(goal: str, fields: str) -> list[str]:
    if fields:
        return [item.strip() for item in fields.split(",") if item.strip()]
    lowered = (goal or "").lower()
    requested = ["title", "price", "image_src"]
    if any(word in lowered for word in ("body", "description", "详情", "描述")):
        requested.append("body")
    return requested

def _draft_plan_from_scout(scout: dict[str, Any], goal: str, fields: str,
                           max_items: int, output: str) -> dict[str, Any]:
    requested = _requested_fields(goal, fields)
    plan = dict(scout.get("recommended_plan", {}))
    plan["goal"] = goal
    plan["max_items"] = max(1, min(int(max_items), 1000))
    plan["output"] = output
    available_fields = plan.get("fields", {})
    plan["fields"] = {
        field: available_fields[field]
        for field in requested
        if field in available_fields and available_fields[field]
    }
    defaults = {
        "title": "h1",
        "price": "[class*=price], [itemprop=price], meta[itemprop=price]",
        "image_src": "img@src",
        "body": "[class*=description], article, main",
    }
    for field in requested:
        if field not in plan["fields"] and field in defaults:
            plan["fields"][field] = defaults[field]
    if not plan["fields"]:
        plan["fields"] = available_fields
    reasons = []
    confidence_parts = []
    if plan.get("list_selector"):
        first = scout.get("link_candidates", [{}])[0]
        reasons.append(f"使用列表 selector {plan['list_selector']}，样本数 {first.get('count', 0)}。")
        confidence_parts.append(min(1.0, first.get("count", 0) / max(1, plan["max_items"])))
    elif plan.get("detail_urls"):
        reasons.append(f"未找到 DOM 列表 selector，改用脚本 URL fallback，共 {len(plan['detail_urls'])} 条候选。")
        confidence_parts.append(0.55)
    if plan.get("fields"):
        reasons.append("字段 selector 来自页面候选排序。")
        confidence_parts.append(min(1.0, len(plan["fields"]) / max(1, len(requested))))
    if scout.get("menu_candidates"):
        reasons.append(f"发现菜单候选 {scout['menu_candidates'][0]['path']}，可用于目录重建。")
        top_menu = scout["menu_candidates"][0]
        plan["menu_source_path"] = top_menu["path"]
        menu_sources = scout.get("_initial_state_sources", [])
        resolved = _resolve_initial_state_path(menu_sources, top_menu["path"]) if menu_sources else None
        if resolved:
            menu_tree = _menu_to_tree(resolved["value"], base_url=plan.get("base_url", plan.get("start_url", "")))
            category_urls = _flatten_menu_urls(menu_tree["items"], max_urls=50)
            if category_urls:
                plan["category_urls"] = category_urls
                reasons.append(f"从菜单来源抽取 {len(category_urls)} 个目录 URL。")
    if "js_rendering_likely_required" in scout.get("access", {}).get("findings", []):
        reasons.append("页面疑似 JS shell，计划已建议 browser 模式。")
    plan = _add_plan_metadata(plan, scout=scout)
    confidence = round(sum(confidence_parts) / len(confidence_parts), 3) if confidence_parts else 0.0
    return {
        "plan": plan,
        "confidence": confidence,
        "recommendation": "ready_to_validate" if confidence >= 0.6 else "needs_review",
        "reasons": reasons,
        "scout_summary": {
            "ok": scout.get("ok"),
            "page": scout.get("page"),
            "top_menu_candidate": (scout.get("menu_candidates") or [None])[0],
            "top_link_candidate": (scout.get("link_candidates") or [None])[0],
            "script_url_count": scout.get("script_url_candidates", {}).get("count", 0),
        },
    }

def _extract_product_from_html(html: str, url: str, fields: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    field_defs = json.loads(fields or "{}")
    if not isinstance(field_defs, dict):
        raise ValueError("fields 必须是 JSON 对象")
    result = {"url": url}
    for name, selector in field_defs.items():
        if not isinstance(selector, str):
            raise ValueError(f"字段 {name} 的 selector 必须是字符串")
        if "@" in selector:
            css, attr = selector.rsplit("@", 1)
            elem = soup.select_one(css)
            result[name] = elem.get(attr, "") if elem else ""
        else:
            elems = soup.select(selector)
            if len(elems) == 1:
                result[name] = elems[0].get_text(strip=True)
            elif len(elems) > 1:
                result[name] = [e.get_text(strip=True) for e in elems]
            else:
                result[name] = ""
    return result

def _run_crawl_job(job_id: str) -> None:
    job = _read_job(job_id)
    cfg = job["config"]
    started = time.time()
    job.update({
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "error": "",
    })
    _write_job(job)
    _append_event({"event": "job", "job_id": job_id, "status": "running", "job_type": cfg["job_type"]})

    try:
        html = _smart_fetch(
            cfg["url"],
            use_cache=cfg["use_cache"],
            mode=cfg["mode"],
            respect_robots=cfg["respect_robots"],
            allow_private=cfg["allow_private"],
        )
        job_type = cfg["job_type"]
        artifact = ""
        db_result = ""

        if job_type == "fetch":
            artifact_path = _safe_job_output_path(job_id, cfg.get("output_name", ""), ".html")
            artifact_path.write_text(html, encoding="utf-8")
            artifact = str(artifact_path)
            result = {
                "content_length": len(html),
                "preview": html[:1000],
                "artifact": artifact,
            }
        elif job_type == "crawl_list":
            links = _extract_links_from_html(
                html,
                cfg["url"],
                cfg["selector"],
                cfg.get("base_url", ""),
                cfg["max_items"],
            )
            artifact_path = _safe_job_output_path(job_id, cfg.get("output_name", ""), ".json")
            artifact_path.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact = str(artifact_path)
            result = {"count": len(links), "links": links, "artifact": artifact}
            if cfg["save_to_db"]:
                db_result = save_batch_to_db(
                    json.dumps(links, ensure_ascii=False),
                    db_name=cfg["db_name"],
                    table=cfg["table"],
                )
        elif job_type == "crawl_product":
            data = _extract_product_from_html(html, cfg["url"], cfg["fields"])
            artifact_path = _safe_job_output_path(job_id, cfg.get("output_name", ""), ".json")
            artifact_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact = str(artifact_path)
            result = {"data": data, "artifact": artifact}
            if cfg["save_to_db"]:
                db_result = save_to_db(
                    json.dumps(data, ensure_ascii=False),
                    db_name=cfg["db_name"],
                    table=cfg["table"],
                )
        else:
            raise ValueError("job_type 只支持 fetch/crawl_list/crawl_product")

        job.update({
            "status": "completed",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": round((time.time() - started) * 1000),
            "artifact": artifact,
            "db_result": db_result,
            "result": result,
        })
        _write_job(job)
        _append_event({
            "event": "job",
            "job_id": job_id,
            "status": "completed",
            "job_type": job_type,
            "duration_ms": job["duration_ms"],
        })
    except Exception as exc:
        job.update({
            "status": "failed",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": round((time.time() - started) * 1000),
            "error": str(exc),
            "error_type": type(exc).__name__,
        })
        _write_job(job)
        _append_event({
            "event": "job",
            "job_id": job_id,
            "status": "failed",
            "job_type": cfg.get("job_type"),
            "duration_ms": job["duration_ms"],
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        })

# ============ 错误类型 ============

def _error_result(msg: str, err_type: str = "unknown", suggestion: str = "") -> str:
    return json.dumps({"success": False, "error": True, "type": err_type, "message": msg, "suggestion": suggestion}, ensure_ascii=False)

def _success_result(data) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)

def _score_fetch_candidate(html: str, target_selector: str = "") -> dict[str, Any]:
    text = html or ""
    lowered = text.lower()
    diagnostics = _diagnose_html(text)
    selector_hits = 0
    if target_selector and text:
        try:
            selector_hits = len(BeautifulSoup(text, "html.parser").select(target_selector))
        except Exception:
            selector_hits = 0
    score = min(len(text), 500_000) / 1000
    if "application/ld+json" in lowered:
        score += 80
    if "jobposting" in lowered:
        score += 120
    if "__next_data__" in lowered or "window.__appdata" in lowered or "window.__initial_state" in lowered:
        score += 50
    if selector_hits:
        score += selector_hits * 25
    if diagnostics.get("challenge", {}).get("detected"):
        score -= 250
    if diagnostics.get("js_shell", {}).get("likely"):
        score -= 80
    if text.lstrip().startswith("{") and ("fetch_failed" in lowered or '"error"' in lowered):
        score -= 300
    if len(text) < 500:
        score -= 50
    return {
        "score": round(score, 2),
        "length": len(text),
        "selector_hits": selector_hits,
        "has_json_ld": "application/ld+json" in lowered,
        "has_jobposting": "jobposting" in lowered,
        "diagnostics": diagnostics,
    }

def _v5_envelope(ok: bool, data: dict[str, Any] | None = None,
                 diagnostics: dict[str, Any] | None = None,
                 recommendations: list[dict[str, Any]] | None = None,
                 **compat) -> dict[str, Any]:
    payload = dict(compat)
    payload.update({
        "ok": bool(ok),
        "version": SERVER_PROTOCOL_VERSION,
        "data": data or {},
        "diagnostics": diagnostics or {},
        "recommendations": recommendations or [],
    })
    return payload

def _v5_compat(data: dict[str, Any]) -> dict[str, Any]:
    reserved = {"ok", "version", "data", "diagnostics", "recommendations"}
    return {
        key: value for key, value in (data or {}).items()
        if key not in reserved and not str(key).startswith("_")
    }

def _json_or_lines(value: str) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        return [line.strip() for line in value.splitlines() if line.strip()]

def _json_obj(value: str, default: dict | None = None) -> dict:
    if not value:
        return default or {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("必须是 JSON 对象")
    return parsed

def _extract_number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None

def _record_matches_condition(record: dict, condition: str) -> bool:
    condition = (condition or "").strip()
    if not condition:
        return True
    match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|>|<|==|!=|contains|exists)\s*(.*)$", condition)
    if not match:
        raise ValueError(f"filter condition 不支持: {condition}")
    field, op, raw_expected = match.groups()
    actual = record.get(field)
    if op == "exists":
        return actual not in (None, "", [], {})
    expected = raw_expected.strip().strip("\"'")
    if op == "contains":
        return expected in str(actual or "")
    if op in {">", ">=", "<", "<="}:
        actual_num = _extract_number(actual)
        expected_num = _extract_number(expected)
        if actual_num is None or expected_num is None:
            return False
        return {
            ">": actual_num > expected_num,
            ">=": actual_num >= expected_num,
            "<": actual_num < expected_num,
            "<=": actual_num <= expected_num,
        }[op]
    if op == "==":
        return str(actual) == expected
    if op == "!=":
        return str(actual) != expected
    return True

def _parse_pipeline(pipeline_json: str | dict) -> dict:
    pipeline = json.loads(pipeline_json) if isinstance(pipeline_json, str) else pipeline_json
    if not isinstance(pipeline, dict):
        raise ValueError("pipeline 必须是 JSON 对象")
    steps = pipeline.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("pipeline.steps 必须是非空数组")
    return pipeline

def _run_pipeline_spec(pipeline: dict, allow_private: bool = False) -> dict:
    context = {
        "links": [],
        "records": [],
        "artifacts": [],
        "db_results": [],
        "frontier": {},
        "step_results": [],
    }
    default_mode = pipeline.get("mode", "auto")
    default_use_cache = bool(pipeline.get("use_cache", True))
    for index, step in enumerate(pipeline["steps"], start=1):
        if not isinstance(step, dict):
            raise ValueError(f"第 {index} 步必须是对象")
        action = step.get("step") or step.get("action")
        if not action:
            raise ValueError(f"第 {index} 步缺少 step")
        mode = step.get("mode", default_mode)
        use_cache = bool(step.get("use_cache", default_use_cache))

        if action == "crawl_list":
            url = step["url"]
            selector = step.get("selector") or step.get("link_selector")
            if not selector:
                raise ValueError("crawl_list 需要 selector")
            html = _smart_fetch(
                url,
                mode=mode,
                use_cache=use_cache,
                respect_robots=bool(step.get("respect_robots", RESPECT_ROBOTS)),
                allow_private=allow_private or bool(step.get("allow_private", False)),
            )
            links = _extract_links_from_html(
                html,
                url,
                selector,
                step.get("base_url", ""),
                int(step.get("max_items") or step.get("max_links") or 100),
            )
            context["links"] = links
            context["step_results"].append({"step": action, "count": len(links)})
        elif action == "crawl_lists":
            urls = step.get("urls") or []
            selector = step.get("selector") or step.get("link_selector")
            if not urls or not isinstance(urls, list):
                raise ValueError("crawl_lists 需要 urls 数组")
            if not selector:
                raise ValueError("crawl_lists 需要 selector")
            max_links = int(step.get("max_items") or step.get("max_links") or 100)
            links = []
            per_url = []
            for list_url in urls:
                html = _smart_fetch(
                    str(list_url),
                    mode=mode,
                    use_cache=use_cache,
                    respect_robots=bool(step.get("respect_robots", RESPECT_ROBOTS)),
                    allow_private=allow_private or bool(step.get("allow_private", False)),
                )
                found = _extract_links_from_html(
                    html,
                    str(list_url),
                    selector,
                    step.get("base_url", str(list_url)),
                    max_links,
                )
                for item in found:
                    item["source_list_url"] = str(list_url)
                links.extend(found)
                per_url.append({"url": str(list_url), "count": len(found)})
                if len(links) >= max_links:
                    break
            context["links"] = links[:max_links]
            context["step_results"].append({
                "step": action,
                "count": len(context["links"]),
                "list_count": len(urls),
                "per_url": per_url,
            })
        elif action == "frontier_add":
            source = step.get("source", "links")
            if source == "links":
                urls = [item["url"] for item in context["links"]]
            else:
                urls = [str(item) for item in step.get("urls", [])]
            valid_urls = []
            for item_url in urls:
                _validate_url(item_url, allow_private=allow_private or bool(step.get("allow_private", False)))
                valid_urls.append(item_url)
            result = _frontier.add_urls(
                valid_urls,
                priority=int(step.get("priority", 0)),
                kind=step.get("kind", "detail"),
                depth=int(step.get("depth", 0)),
                parent_url=step.get("parent_url", ""),
                payload=step.get("payload", {}),
            )
            context["frontier"] = result
            context["step_results"].append({"step": action, **result})
        elif action == "frontier_next":
            rows = _frontier.next_batch(
                limit=int(step.get("limit", 20)),
                domain=step.get("domain", ""),
                worker_id=step.get("worker_id", "pipeline"),
                lease_seconds=int(step.get("lease_seconds", 900)),
            )
            context["links"] = [{"url": row["url"], "text": row.get("kind", ""), "frontier_id": row["id"]} for row in rows]
            context["step_results"].append({"step": action, "count": len(rows)})
        elif action in {"crawl_products", "crawl_product"}:
            fields = step.get("fields")
            if not isinstance(fields, dict):
                raise ValueError("crawl_products 需要 fields 对象")
            links = context["links"]
            max_items = min(int(step.get("max_items", len(links))), len(links))
            records = []
            done_ids = []
            failed_ids = []
            for link in links[:max_items]:
                url = link["url"] if isinstance(link, dict) else str(link)
                try:
                    html = _smart_fetch(
                        url,
                        mode=mode,
                        use_cache=use_cache,
                        respect_robots=bool(step.get("respect_robots", RESPECT_ROBOTS)),
                        allow_private=allow_private or bool(step.get("allow_private", False)),
                    )
                    record = _extract_product_from_html(html, url, json.dumps(fields, ensure_ascii=False))
                    if isinstance(link, dict) and link.get("frontier_id"):
                        done_ids.append(int(link["frontier_id"]))
                    records.append(record)
                except Exception:
                    if isinstance(link, dict) and link.get("frontier_id"):
                        failed_ids.append(int(link["frontier_id"]))
                    if bool(step.get("stop_on_error", False)):
                        raise
            if done_ids:
                _frontier.mark_done(done_ids)
            if failed_ids:
                _frontier.mark_failed(failed_ids, error="pipeline crawl failed", retry=True)
            context["records"] = records
            context["step_results"].append({"step": action, "count": len(records), "failed": len(failed_ids)})
        elif action == "filter":
            condition = step.get("condition", "")
            before = len(context["records"])
            context["records"] = [
                record for record in context["records"]
                if _record_matches_condition(record, condition)
            ]
            context["step_results"].append({"step": action, "before": before, "after": len(context["records"])})
        elif action == "save":
            db_name = step.get("db") or step.get("db_name") or pipeline.get("db") or "crawler_data"
            table = step.get("table") or pipeline.get("table") or "items"
            result = save_batch_to_db(
                json.dumps(context["records"], ensure_ascii=False),
                db_name=db_name,
                table=table,
                atomic=bool(step.get("atomic", True)),
            )
            context["db_results"].append(json.loads(result) if result.startswith("{") else result)
            context["step_results"].append({"step": action, "count": len(context["records"]), "db": db_name, "table": table})
        elif action == "save_json":
            filename = step.get("filename", "pipeline_output.json")
            formatted = _format_output_records(context["records"], step.get("format", "records"))
            result = save_data(json.dumps(formatted, ensure_ascii=False, indent=2), filename)
            context["artifacts"].append(result)
            context["step_results"].append({"step": action, "artifact": result, "format": step.get("format", "records")})
        else:
            raise ValueError(f"不支持的 pipeline step: {action}")
    output_format = pipeline.get("output_format", "records")
    formatted_output = _format_output_records(context["records"], output_format)
    return {
        "success": True,
        "steps": context["step_results"],
        "links_count": len(context["links"]),
        "records_count": len(context["records"]),
        "sample": context["records"][:3],
        "output_format": output_format,
        "formatted_sample": formatted_output if isinstance(formatted_output, dict) else formatted_output[:3],
        "artifacts": context["artifacts"],
        "db_results": context["db_results"],
        "frontier": context["frontier"],
    }

# ============ SQLite 连接管理 ============

class SQLiteConnectionPool:
    def __init__(self, max_size: int = DB_POOL_SIZE):
        self._max_size = max(1, max_size)
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def _new_connection(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextlib.contextmanager
    def connection(self, db_path: Path, row_factory=None):
        key = str(db_path.resolve())
        with self._lock:
            item = self._items.get(key)
            if item:
                self._items.move_to_end(key)
            else:
                while len(self._items) >= self._max_size:
                    _old_key, old_item = self._items.popitem(last=False)
                    with contextlib.suppress(Exception):
                        old_item["conn"].close()
                item = {"conn": self._new_connection(db_path), "lock": threading.RLock()}
                self._items[key] = item

        conn = item["conn"]
        with item["lock"]:
            previous_row_factory = conn.row_factory
            if row_factory is not None:
                conn.row_factory = row_factory
            try:
                yield conn
            finally:
                conn.row_factory = previous_row_factory

    def close_all(self) -> None:
        with self._lock:
            for item in self._items.values():
                with contextlib.suppress(Exception):
                    item["conn"].close()
            self._items.clear()

_db_pool = SQLiteConnectionPool()

def _validate_identifier(name: str, label: str = "标识符") -> str:
    if not SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"{label}包含非法字符: {name}")
    return name

def _quote_identifier(name: str) -> str:
    _validate_identifier(name)
    return f'"{name}"'

def _validate_record_keys(data: dict) -> None:
    for key in data:
        _validate_identifier(str(key), "字段名")
        if key in RESERVED_DB_COLUMNS:
            raise ValueError(f"字段名为系统保留字段: {key}")

def _get_table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    return {row[1] for row in cursor.fetchall()}

def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None

def _build_where_clause(where: str, allowed_columns: set[str]) -> tuple[str, list]:
    """Build a safe WHERE clause from JSON, rejecting raw SQL strings."""
    if not where:
        return "", []
    try:
        spec = json.loads(where)
    except json.JSONDecodeError as exc:
        raise ValueError("where 只接受 JSON：例如 {\"title\": \"Shoes\"} 或 [{\"field\":\"price\",\"op\":\">\",\"value\":10}]") from exc

    params: list = []
    clauses: list[str] = []
    allowed_ops = {"=", "!=", ">", ">=", "<", "<=", "LIKE"}

    if isinstance(spec, dict):
        items = [{"field": key, "op": "=", "value": value} for key, value in spec.items()]
    elif isinstance(spec, list):
        items = spec
    else:
        raise ValueError("where JSON 必须是对象或条件数组")

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("where 条件必须是对象")
        field = str(item.get("field", ""))
        op = str(item.get("op", "=")).upper()
        value = item.get("value")
        _validate_identifier(field, "where 字段名")
        if field not in allowed_columns:
            raise ValueError(f"where 字段不存在: {field}")
        if op not in allowed_ops:
            raise ValueError(f"where 操作符不允许: {op}")
        # v4.0: LIKE 操作符的 value 自动 escape SQL 通配符 % 和 _，
        # 并强制为"包含字面量"语义（两端加 %），防止用户传入恶意通配
        if op == "LIKE":
            raw = str(value or "")
            escaped = raw.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            clauses.append(f"{_quote_identifier(field)} LIKE ? ESCAPE '\\'")
            params.append(pattern)
        else:
            clauses.append(f"{_quote_identifier(field)} {op} ?")
            params.append(value)

    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

# ============ 企业数据 Schema ============

SQLITE_COLUMN_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC"}

def _schema_file(db_name: str, table: str) -> Path:
    _validate_identifier(db_name, "数据库名")
    _validate_identifier(table, "表名")
    return SCHEMA_DIR / f"{db_name}.{table}.json"

def _load_schema(db_name: str, table: str, schema: str = "") -> dict | None:
    if schema:
        try:
            raw = json.loads(schema) if isinstance(schema, str) else schema
        except json.JSONDecodeError as exc:
            raise ValueError("schema 必须是 JSON 对象") from exc
        return _normalise_schema(raw)

    path = _schema_file(db_name, table)
    if not path.exists():
        return None
    return _normalise_schema(json.loads(path.read_text(encoding="utf-8")))

def _normalise_schema(schema: dict) -> dict:
    if not isinstance(schema, dict):
        raise ValueError("schema 必须是 JSON 对象")

    columns = schema.get("columns") or schema.get("fields")
    if not isinstance(columns, dict) or not columns:
        raise ValueError("schema.columns 必须是非空对象，例如 {\"url\":\"TEXT\",\"price\":\"REAL\"}")

    normalised_columns: dict[str, str] = {}
    for name, col_type in columns.items():
        _validate_identifier(str(name), "schema 字段名")
        sql_type = str(col_type or "TEXT").upper()
        if sql_type not in SQLITE_COLUMN_TYPES:
            raise ValueError(f"字段 {name} 类型不支持: {col_type}")
        if name in RESERVED_DB_COLUMNS:
            raise ValueError(f"字段名为系统保留字段: {name}")
        normalised_columns[str(name)] = sql_type

    required = [str(x) for x in schema.get("required", [])]
    unique = schema.get("unique") or schema.get("unique_fields") or []
    if isinstance(unique, str):
        unique = [unique]
    unique = [str(x) for x in unique]

    indexes = []
    for index in schema.get("indexes", []):
        if isinstance(index, str):
            fields = [index]
        else:
            fields = [str(x) for x in index]
        if fields:
            indexes.append(fields)

    for field in [*required, *unique, *(f for index in indexes for f in index)]:
        _validate_identifier(field, "schema 字段名")
        if field not in normalised_columns:
            raise ValueError(f"schema 引用了未声明字段: {field}")

    return {
        "columns": normalised_columns,
        "required": required,
        "unique": unique,
        "indexes": indexes,
        "strict": bool(schema.get("strict", True)),
    }

def _infer_sqlite_type(value) -> str:
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"

def _coerce_db_value(value, col_type: str = "TEXT"):
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if value is None:
        return None
    if col_type == "INTEGER":
        return int(value)
    if col_type == "REAL":
        return float(value)
    if col_type == "TEXT":
        return str(value)
    return value

def _prepare_record(data_dict: dict, schema: dict | None) -> dict:
    _validate_record_keys(data_dict)
    if not schema:
        return {
            key: _coerce_db_value(value, _infer_sqlite_type(value))
            for key, value in data_dict.items()
        }

    columns = schema["columns"]
    missing = [field for field in schema["required"] if data_dict.get(field) in (None, "")]
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")

    unknown = [field for field in data_dict if field not in columns]
    if schema["strict"] and unknown:
        raise ValueError(f"schema 未声明字段: {', '.join(unknown)}")

    return {
        key: _coerce_db_value(value, columns.get(key, "TEXT"))
        for key, value in data_dict.items()
        if key in columns or not schema["strict"]
    }

def _make_sole_id(data_dict: dict, schema: dict | None) -> str:
    if schema and schema["unique"]:
        identity = {field: data_dict.get(field) for field in schema["unique"]}
        raw = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    else:
        raw = data_dict.get("url", "") + data_dict.get("handle", "") + json.dumps(data_dict, ensure_ascii=False, sort_keys=True)[:200]
    return hashlib.md5(raw.encode()).hexdigest()

def _ensure_table(cursor: sqlite3.Cursor, table: str, record: dict, schema: dict | None) -> None:
    table_sql = _quote_identifier(table)
    if schema:
        expected_columns = dict(schema["columns"])
        if not schema["strict"]:
            for key, value in record.items():
                expected_columns.setdefault(key, _infer_sqlite_type(value))
    else:
        expected_columns = {
            key: _infer_sqlite_type(value) for key, value in record.items()
        }

    if not _table_exists(cursor, table):
        columns = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for key, col_type in expected_columns.items():
            columns.append(f"{_quote_identifier(key)} {col_type}")
        columns.append("sole_id TEXT UNIQUE")
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_sql} ({', '.join(columns)})")
    else:
        existing_cols = _get_table_columns(cursor, table)
        for key, col_type in expected_columns.items():
            if key not in existing_cols:
                cursor.execute(f"ALTER TABLE {table_sql} ADD COLUMN {_quote_identifier(key)} {col_type}")

def _ensure_schema_indexes(cursor: sqlite3.Cursor, table: str, schema: dict | None) -> None:
    if not schema:
        return
    table_sql = _quote_identifier(table)
    index_specs = []
    if schema["unique"]:
        index_specs.append((schema["unique"], True))
    index_specs.extend((fields, False) for fields in schema["indexes"])

    for fields, unique in index_specs:
        suffix = "_".join(fields)
        index_name = f"idx_{table}_{suffix}"
        _validate_identifier(index_name[:120], "索引名")
        columns = ", ".join(_quote_identifier(field) for field in fields)
        unique_sql = "UNIQUE " if unique else ""
        cursor.execute(
            f"CREATE {unique_sql}INDEX IF NOT EXISTS {_quote_identifier(index_name[:120])} "
            f"ON {table_sql} ({columns})"
        )

def _insert_record(cursor: sqlite3.Cursor, table: str, record: dict, schema: dict | None) -> tuple[bool, str]:
    table_sql = _quote_identifier(table)
    sole_id = _make_sole_id(record, schema)
    cursor.execute(f"SELECT 1 FROM {table_sql} WHERE sole_id = ?", (sole_id,))
    if cursor.fetchone():
        return False, sole_id

    values_record = dict(record)
    values_record["sole_id"] = sole_id
    fields = list(values_record.keys())
    values = tuple(values_record[f] for f in fields)
    placeholders = ",".join(["?"] * len(fields))
    field_names = ",".join(_quote_identifier(f) for f in fields)
    cursor.execute(f"INSERT INTO {table_sql} ({field_names}) VALUES ({placeholders})", values)
    return True, sole_id

def _browser_launch_args() -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--window-size=1920,1080",
    ]
    if BROWSER_ALLOW_UNSAFE_FLAGS:
        args.extend([
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
        ])
    return args

def _retry_delay_from_response(resp, attempt: int) -> float:
    retry_after = getattr(resp, "headers", {}).get("Retry-After", "")
    if retry_after:
        try:
            return min(float(retry_after), RETRY_MAX_DELAY)
        except ValueError:
            pass
    return min(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), RETRY_MAX_DELAY)

DEFAULT_CHALLENGE_PATTERNS = [
    "cf-challenge",
    "cf-browser-verification",
    "checking your browser",
    "just a moment",
    "captcha",
    "hcaptcha",
    "recaptcha",
    "geetest",
    "请完成安全验证",
    "人机验证",
    "验证码",
    "安全验证",
]
CHALLENGE_PATTERNS = _env_list("CRAWLER_CHALLENGE_PATTERNS", DEFAULT_CHALLENGE_PATTERNS)

def _detect_challenge_page(html: str) -> str:
    if not DETECT_CHALLENGE_PAGES:
        return ""
    sample = html[:200000].lower()
    for pattern in CHALLENGE_PATTERNS:
        if pattern.lower() in sample:
            return pattern
    return ""

def _registered_tool_count() -> int | None:
    """读取 MCP 注册工具数。优先 FastMCP 内部 _tool_manager（旧行为，测试依赖），
    若 _tool_manager 自身缺失才回退到自维护计数器。"""
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        counter = globals().get("_TOOL_REGISTRATION_COUNT")
        if isinstance(counter, int) and counter > 0:
            return counter
        return None
    try:
        tools = getattr(manager, "_tools", None)
        if isinstance(tools, dict):
            return len(tools)
    except Exception as exc:
        logger.debug(f"无法读取 MCP 工具数量: {exc}")
    return None


# v4.0: 工具注册计数器 + 装饰器封装，作为 _tool_manager 失败时的 fallback
_TOOL_REGISTRATION_COUNT = 0
_TOOL_NAMES: list[str] = []
_original_tool_decorator = mcp.tool


def _tracked_tool(*tool_args, **tool_kwargs):
    decorator = _original_tool_decorator(*tool_args, **tool_kwargs)
    def wrap(fn):
        global _TOOL_REGISTRATION_COUNT
        _TOOL_REGISTRATION_COUNT += 1
        _TOOL_NAMES.append(fn.__name__)
        return decorator(fn)
    return wrap


mcp.tool = _tracked_tool

# ============ HTTP 引擎 ============

class HTTPEngine:
    def __init__(self):
        self._requests_sessions: OrderedDict[str, object] = OrderedDict()
        self._requests_lock = threading.Lock()
        self._curl_sessions: OrderedDict[str, object] = OrderedDict()
        self._curl_lock = threading.Lock()
        self._pw = None
        self._pw_browser = None
        self._pw_contexts: OrderedDict[str, object] = OrderedDict()  # domain -> context
        self._pw_lock = threading.Lock()
        self._browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-worker")
        self._browser_thread_id: int | None = None

    # ---------- requests 模式 ----------
    def _get_requests_session(self, domain: str) -> _requests_lib.Session:
        with self._requests_lock:
            if domain in self._requests_sessions:
                self._requests_sessions.move_to_end(domain)
            else:
                while len(self._requests_sessions) >= MAX_DOMAIN_SESSIONS:
                    _old_domain, old_session = self._requests_sessions.popitem(last=False)
                    with contextlib.suppress(Exception):
                        old_session.close()
                session = _requests_lib.Session()
                if PERSIST_COOKIES:
                    session.cookies.update(_cookie_store.load(domain))
                self._requests_sessions[domain] = session
            return self._requests_sessions[domain]

    def fetch_with_requests(self, url: str, headers: dict | None = None,
                            proxy: dict | None = None, timeout: int = REQUEST_TIMEOUT,
                            cookies: dict | None = None, verify_tls: bool = VERIFY_TLS,
                            pinned=None) -> str:
        domain = urlparse(url).netloc.lower()
        session = self._get_requests_session(domain)
        if cookies:
            session.cookies.update(cookies)
        # v4.0: DNS pinning - 走代理时跳过（代理会做远程解析）
        request_url = url
        request_headers = dict(headers or _get_headers()[0])
        if pinned and not proxy and not (urlparse(url).scheme == "https" and verify_tls):
            request_url = _dns_pin_mod.rewrite_url_to_ip(url, pinned)
            request_headers["Host"] = pinned.hostname
            # 不校验 hostname assert（IP 替换会让 SSL 校验需要特殊处理）
            # urllib3 会用 SNI 取自连接 hostname，这里保留默认 verify 即可
        last_error = None
        for attempt in range(REQUEST_RETRY):
            try:
                resp = session.get(request_url, headers=request_headers, proxies=proxy,
                                   timeout=timeout, verify=verify_tls)
                if resp.status_code == 429:
                    retry_after = _retry_delay_from_response(resp, attempt)
                    logger.warning(f"429 限流，等待 {retry_after}s")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding
                if PERSIST_COOKIES:
                    _cookie_store.save(domain, session.cookies.get_dict())
                return resp.text
            except Exception as e:
                last_error = e
                delay = min(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), RETRY_MAX_DELAY)
                logger.warning(f"请求失败 (尝试 {attempt+1}/{REQUEST_RETRY}): {e}, 等待 {delay:.1f}s")
                time.sleep(delay)
                if proxy:
                    proxy = _proxy_pool.get_proxy()
        raise Exception(f"请求失败 (已重试 {REQUEST_RETRY} 次): {last_error}")

    # ---------- curl_cffi 模式 ----------
    def _get_curl_session(self, domain: str) -> CurlCffiSession:
        with self._curl_lock:
            if domain in self._curl_sessions:
                self._curl_sessions.move_to_end(domain)
            else:
                while len(self._curl_sessions) >= MAX_DOMAIN_SESSIONS:
                    _old_domain, old_session = self._curl_sessions.popitem(last=False)
                    with contextlib.suppress(Exception):
                        old_session.close()
                session = CurlCffiSession()
                if PERSIST_COOKIES:
                    with contextlib.suppress(Exception):
                        session.cookies.update(_cookie_store.load(domain))
                self._curl_sessions[domain] = session
            return self._curl_sessions[domain]

    def fetch_with_curl_cffi(self, url: str, headers: dict | None = None,
                              proxy: dict | None = None, timeout: int = REQUEST_TIMEOUT,
                              impersonate: str | None = None, cookies: dict | None = None,
                              verify_tls: bool = VERIFY_TLS, pinned=None) -> str:
        if not HAS_CURL_CFFI:
            raise Exception("curl_cffi 未安装")

        domain = urlparse(url).netloc.lower()
        session = self._get_curl_session(domain)
        if cookies:
            try:
                session.cookies.update(cookies)
            except Exception as exc:
                logger.warning(f"curl_cffi cookies 写入失败: {exc}", exc_info=True)

        if not impersonate:
            ua = (headers or {}).get("User-Agent", _get_random_ua())
            impersonate = _get_matching_impersonate(ua)

        # v4.0: DNS pinning via curl 原生 resolve 参数（仅新版 curl_cffi 支持）
        resolve_entries = None
        if pinned and not proxy and CURL_CFFI_SUPPORTS_RESOLVE:
            resolve_entries = [pinned.curl_resolve_entry()]

        last_error = None
        for attempt in range(REQUEST_RETRY):
            start_time = time.time()
            try:
                kwargs = dict(
                    headers=headers or _get_headers()[0],
                    impersonate=impersonate,
                    proxies=proxy,
                    timeout=timeout,
                    verify=verify_tls,
                    cookies=cookies,
                )
                if resolve_entries:
                    kwargs["resolve"] = resolve_entries
                resp = session.get(url, **kwargs)
                if resp.status_code == 429:
                    retry_after = _retry_delay_from_response(resp, attempt)
                    logger.warning(f"curl_cffi 429 限流，等待 {retry_after}s")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                if proxy:
                    _proxy_pool.report_success(proxy, time.time() - start_time)
                if PERSIST_COOKIES:
                    try:
                        _cookie_store.save(domain.lower(), session.cookies.get_dict())
                    except Exception as exc:
                        logger.warning(f"curl_cffi cookies 持久化失败: {exc}", exc_info=True)
                return resp.text
            except Exception as e:
                last_error = e
                if proxy:
                    _proxy_pool.report_failure(proxy)
                delay = min(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), RETRY_MAX_DELAY)
                logger.warning(f"curl_cffi 失败 (尝试 {attempt+1}/{REQUEST_RETRY}): {e}, 等待 {delay:.1f}s")
                time.sleep(delay)
                if proxy:
                    proxy = _proxy_pool.get_proxy()
        raise Exception(f"curl_cffi 失败 (已重试 {REQUEST_RETRY} 次): {last_error}")

    # ---------- POST 请求 ----------
    def post_with_requests(self, url: str, data: str | dict | None = None,
                           content_type: str = "application/json", headers: dict | None = None,
                           proxy: dict | None = None, timeout: int = REQUEST_TIMEOUT,
                           verify_tls: bool = VERIFY_TLS, pinned=None) -> str:
        if not headers:
            headers, _ = _get_headers()
        request_headers = dict(headers)
        if content_type != "application/json":
            request_headers["Content-Type"] = content_type
        domain = urlparse(url).netloc.lower()
        session = self._get_requests_session(domain)
        # v4.0: DNS pinning
        request_url = url
        if pinned and not proxy and not (urlparse(url).scheme == "https" and verify_tls):
            request_url = _dns_pin_mod.rewrite_url_to_ip(url, pinned)
            request_headers["Host"] = pinned.hostname
        last_error = None
        for attempt in range(REQUEST_RETRY):
            start_time = time.time()
            try:
                if content_type == "application/json":
                    body = json.loads(data) if isinstance(data, str) else data
                    resp = session.post(request_url, json=body, headers=request_headers, proxies=proxy,
                                        timeout=timeout, verify=verify_tls)
                else:
                    resp = session.post(request_url, data=data, headers=request_headers, proxies=proxy,
                                        timeout=timeout, verify=verify_tls)
                if resp.status_code == 429:
                    retry_after = _retry_delay_from_response(resp, attempt)
                    logger.warning(f"POST 429 限流，等待 {retry_after}s")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding
                if proxy:
                    _proxy_pool.report_success(proxy, time.time() - start_time)
                if PERSIST_COOKIES:
                    _cookie_store.save(domain, session.cookies.get_dict())
                return resp.text
            except Exception as e:
                last_error = e
                if proxy:
                    _proxy_pool.report_failure(proxy)
                    proxy = _proxy_pool.get_proxy()
                delay = min(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), RETRY_MAX_DELAY)
                logger.warning(f"POST 请求失败 (尝试 {attempt+1}/{REQUEST_RETRY}): {e}, 等待 {delay:.1f}s")
                time.sleep(delay)
        raise Exception(f"POST 请求失败 (已重试 {REQUEST_RETRY} 次): {last_error}")

    # ---------- Playwright 浏览器 ----------
    def _run_browser_task(self, func, *args, **kwargs):
        if self._browser_thread_id == threading.get_ident():
            return func(*args, **kwargs)
        future = self._browser_executor.submit(func, *args, **kwargs)
        return future.result(timeout=(BROWSER_TIMEOUT / 1000) + 120)

    def _ensure_browser(self):
        self._browser_thread_id = threading.get_ident()
        with self._pw_lock:
            if self._pw_browser is not None:
                return
            if not HAS_PLAYWRIGHT:
                raise Exception("Playwright 未安装，请运行: pip install playwright && playwright install chromium")
            self._pw = sync_playwright().start()
            self._pw_browser = self._pw.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=_browser_launch_args(),
            )
            logger.info("Playwright 浏览器已启动")

    def _get_context_for_domain(self, domain: str):
        with self._pw_lock:
            if domain in self._pw_contexts:
                self._pw_contexts.move_to_end(domain)
                return self._pw_contexts[domain]

            while len(self._pw_contexts) >= MAX_BROWSER_CONTEXTS:
                _old_domain, old_context = self._pw_contexts.popitem(last=False)
                with contextlib.suppress(Exception):
                    self._save_browser_context_state(_old_domain, old_context)
                    old_context.close()

            ua = _get_random_ua()
            context_options = {
                "user_agent": ua,
                "viewport": {"width": random.choice([1920, 1440, 1366]), "height": random.choice([1080, 900])},
                "locale": random.choice(["zh-CN", "en-US"]),
                "timezone_id": random.choice(["Asia/Shanghai", "America/New_York", "Europe/London"]),
                "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
                "ignore_https_errors": not VERIFY_TLS,
            }
            state_path = _browser_storage_state_path(domain)
            if PERSIST_COOKIES and state_path.exists():
                context_options["storage_state"] = str(state_path)
            context = self._pw_browser.new_context(**context_options)
            # 增强反检测脚本
            context.add_init_script("""
                // 隐藏 webdriver
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                // 插件伪装
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                // Chrome 对象
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
                // 删除 CDC 属性
                for (let key of Object.keys(window)) {
                    if (key.startsWith('cdc_')) delete window[key];
                }
                // Canvas 指纹加噪
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {
                    const ctx = this.getContext('2d');
                    if (ctx) {
                        const style = ctx.fillStyle;
                        ctx.fillStyle = 'rgba(0,0,0,0.01)';
                        ctx.fillRect(0, 0, 1, 1);
                        ctx.fillStyle = style;
                    }
                    return origToDataURL.apply(this, arguments);
                };
                // WebGL 指纹伪装
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.apply(this, arguments);
                };
                // AudioContext 指纹加噪
                const origCreateOscillator = AudioContext.prototype.createOscillator;
                AudioContext.prototype.createOscillator = function() {
                    const osc = origCreateOscillator.apply(this, arguments);
                    const origStart = osc.start;
                    osc.start = function() {
                        this.frequency.value += Math.random() * 0.001;
                        return origStart.apply(this, arguments);
                    };
                    return osc;
                };
                // Permission API 伪装
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters);
                // 屏幕尺寸一致性
                Object.defineProperty(screen, 'width', { get: () => window.innerWidth });
                Object.defineProperty(screen, 'height', { get: () => window.innerHeight });
            """)
            self._pw_contexts[domain] = context
            return context

    def _save_browser_context_state(self, domain: str, context) -> None:
        if not PERSIST_COOKIES:
            return
        with contextlib.suppress(Exception):
            context.storage_state(path=str(_browser_storage_state_path(domain)))

    def fetch_with_browser(self, url: str, wait_until: str = "domcontentloaded",
                           render_time: float = BROWSER_RENDER_TIME,
                           wait_selector: str = "", scroll_count: int = 0,
                           scroll_delay: float = 1.0) -> str:
        return self._run_browser_task(
            self._fetch_with_browser_sync,
            url,
            wait_until,
            render_time,
            wait_selector,
            scroll_count,
            scroll_delay,
        )

    def _fetch_with_browser_sync(self, url: str, wait_until: str = "domcontentloaded",
                                 render_time: float = BROWSER_RENDER_TIME,
                                 wait_selector: str = "", scroll_count: int = 0,
                                 scroll_delay: float = 1.0) -> str:
        self._ensure_browser()
        domain = urlparse(url).netloc.lower()
        context = self._get_context_for_domain(domain)
        page = context.new_page()
        try:
            page.set_default_timeout(BROWSER_TIMEOUT)
            response = page.goto(url, wait_until=wait_until, timeout=BROWSER_TIMEOUT)
            if response and response.status == 429:
                raise RuntimeError("浏览器请求被限流: HTTP 429")
            if response and response.status >= 400:
                raise RuntimeError(f"浏览器请求失败: HTTP {response.status}")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15000)
                except Exception:
                    logger.warning(f"等待元素超时: {wait_selector}")
            if render_time > 0:
                time.sleep(render_time)
            # 无限滚动支持
            for i in range(scroll_count):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(scroll_delay)
            html = page.content()
            challenge = _detect_challenge_page(html)
            if challenge:
                raise RuntimeError(f"浏览器返回疑似验证码/人机验证页面: {challenge}")
            self._save_browser_context_state(domain, context)
            return html
        finally:
            page.close()

    def observe_network(self, url: str, wait_until: str = "domcontentloaded",
                        render_time: float = BROWSER_RENDER_TIME,
                        wait_selector: str = "", scroll_count: int = 0,
                        scroll_delay: float = 1.0,
                        capture_resource_types: set[str] | None = None,
                        max_entries: int = 200,
                        capture_json_sample: bool = False,
                        max_json_sample_bytes: int = 20000) -> dict[str, Any]:
        return self._run_browser_task(
            self._observe_network_sync,
            url,
            wait_until,
            render_time,
            wait_selector,
            scroll_count,
            scroll_delay,
            capture_resource_types or {"xhr", "fetch", "document"},
            max_entries,
            capture_json_sample,
            max_json_sample_bytes,
        )

    def _observe_network_sync(self, url: str, wait_until: str = "domcontentloaded",
                              render_time: float = BROWSER_RENDER_TIME,
                              wait_selector: str = "", scroll_count: int = 0,
                              scroll_delay: float = 1.0,
                              capture_resource_types: set[str] | None = None,
                              max_entries: int = 200,
                              capture_json_sample: bool = False,
                              max_json_sample_bytes: int = 20000) -> dict[str, Any]:
        self._ensure_browser()
        domain = urlparse(url).netloc.lower()
        context = self._get_context_for_domain(domain)
        page = context.new_page()
        entries: list[dict[str, Any]] = []
        capture_resource_types = capture_resource_types or {"xhr", "fetch", "document"}
        max_entries = max(1, min(int(max_entries), 1000))

        def should_capture(resource_type: str, response_url: str, content_type: str) -> bool:
            if resource_type in capture_resource_types:
                return True
            if "json" in (content_type or "").lower():
                return True
            return bool(NETWORK_DATA_KEYWORDS.search(response_url or "")) and not _looks_like_static_asset_url(response_url or "")

        def on_response(response):
            if len(entries) >= max_entries:
                return
            with contextlib.suppress(Exception):
                req = response.request
                resource_type = req.resource_type
                response_url = response.url
                headers = response.headers or {}
                content_type = headers.get("content-type", "")
                if not should_capture(resource_type, response_url, content_type):
                    return
                item = {
                    "url": response_url,
                    "method": req.method,
                    "resource_type": resource_type,
                    "status": response.status,
                    "content_type": content_type.split(";")[0],
                    "pagination_params": _pagination_params_from_url(response_url),
                    "json_like": _is_json_like_url(response_url) or "json" in content_type.lower(),
                    "post_data_preview": (req.post_data or "")[:500] if req.method != "GET" else "",
                }
                content_length = headers.get("content-length", "")
                if content_length:
                    item["content_length"] = content_length
                if capture_json_sample and item["json_like"]:
                    try:
                        if not content_length or int(content_length) <= max_json_sample_bytes:
                            text = response.text()
                            item["sample_text"] = text[:max_json_sample_bytes]
                            with contextlib.suppress(Exception):
                                parsed = json.loads(text)
                                if isinstance(parsed, dict):
                                    item["sample_json_keys"] = list(parsed.keys())[:30]
                                elif isinstance(parsed, list):
                                    item["sample_json_type"] = "list"
                                    item["sample_json_length"] = len(parsed)
                    except Exception as exc:
                        item["sample_error"] = str(exc)[:200]
                entries.append(item)

        page.on("response", on_response)
        try:
            page.set_default_timeout(BROWSER_TIMEOUT)
            main_response = page.goto(url, wait_until=wait_until, timeout=BROWSER_TIMEOUT)
            if wait_selector:
                with contextlib.suppress(Exception):
                    page.wait_for_selector(wait_selector, timeout=15000)
            if render_time > 0:
                time.sleep(render_time)
            for _ in range(scroll_count):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(scroll_delay)
            if capture_resource_types:
                time.sleep(min(1.5, max(0.3, render_time / 4)))
            html = page.content()
            challenge = _detect_challenge_page(html)
            if challenge:
                raise RuntimeError(f"浏览器返回疑似验证码/人机验证页面: {challenge}")
            self._save_browser_context_state(domain, context)
            soup = BeautifulSoup(html or "", "html.parser")
            summary = _summarize_network_entries(entries)
            return {
                "url": url,
                "main_status": main_response.status if main_response else None,
                "page": {
                    "html_bytes": len(html or ""),
                    "text_chars": len(soup.get_text(" ", strip=True)),
                    "dom_anchor_count": len(soup.find_all("a", href=True)),
                    "script_count": len(soup.find_all("script")),
                    "truncated_likely": len(html or "") >= max(0, FETCH_MAX_LENGTH - 16),
                },
                "network": summary,
                "recommendations": _network_recommendations(summary),
            }
        finally:
            page.close()

    def take_screenshot(self, url: str, full_page: bool = True, wait_selector: str = "") -> str:
        return self._run_browser_task(self._take_screenshot_sync, url, full_page, wait_selector)

    def _take_screenshot_sync(self, url: str, full_page: bool = True, wait_selector: str = "") -> str:
        self._ensure_browser()
        domain = urlparse(url).netloc.lower()
        context = self._get_context_for_domain(domain)
        page = context.new_page()
        try:
            page.set_default_timeout(BROWSER_TIMEOUT)
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15000)
                except Exception:
                    pass
            time.sleep(2)
            screenshot_path = OUTPUT_DIR / f"screenshot_{hashlib.md5(url.encode()).hexdigest()[:8]}.png"
            page.screenshot(path=str(screenshot_path), full_page=full_page)
            self._save_browser_context_state(domain, context)
            return str(screenshot_path)
        finally:
            page.close()

    def close_browser(self, log: bool = True):
        if self._browser_thread_id == threading.get_ident():
            self._close_browser_sync(log)
            return
        future = self._browser_executor.submit(self._close_browser_sync, log)
        future.result(timeout=30)

    def _close_browser_sync(self, log: bool = True):
        with self._pw_lock:
            for domain, ctx in self._pw_contexts.items():
                try:
                    self._save_browser_context_state(domain, ctx)
                    ctx.close()
                except Exception:
                    pass
            self._pw_contexts.clear()
            if self._pw_browser:
                try:
                    self._pw_browser.close()
                except Exception:
                    pass
                self._pw_browser = None
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
            self._browser_thread_id = None
            if log:
                logger.info("Playwright 浏览器已关闭")

    def close_curl_sessions(self):
        with self._curl_lock:
            for session in self._curl_sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._curl_sessions.clear()

    def close_requests_sessions(self):
        with self._requests_lock:
            for session in self._requests_sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._requests_sessions.clear()

    def shutdown_browser_worker(self):
        self._browser_executor.shutdown(wait=False)

_engine = HTTPEngine()

@atexit.register
def _cleanup():
    # 解释器关停期间，stderr/logger 可能已不可用，所以这里静默吞所有异常
    def _silent(action, label):
        try:
            action()
        except Exception:
            pass

    _silent(lambda: _engine.close_browser(log=False), "browser")
    _silent(_engine.close_curl_sessions, "curl_sessions")
    _silent(_engine.close_requests_sessions, "requests_sessions")
    _silent(_engine.shutdown_browser_worker, "browser_worker")
    _silent(lambda: _job_executor.shutdown(wait=False), "job_executor")
    _silent(lambda: _executor.shutdown(wait=False), "cache_executor")
    _silent(_db_pool.close_all, "db_pool")
    # v4.0: 新增组件关停
    _silent(_frontier.close, "frontier")
    _silent(_async_backend.close, "async_backend")

# ============ 智能请求调度 ============

def _smart_fetch(url: str, headers: str = "{}", use_cache: bool = True,
                 mode: str = "auto", use_proxy: bool = False, cookies: str = "",
                 respect_robots: bool = RESPECT_ROBOTS, rate_limit: float | None = None,
                 verify_tls: bool = VERIFY_TLS, save_cache: bool = True,
                 allow_private: bool = False) -> str:
    """v4.0: 三级 auto-mode 升级 + 域名记忆 + 反爬挑战页探测。

    auto 模式策略：
    1. 查 domain_memory：若有 24h 内成功记忆 → 直接走该模式
    2. 默认从 curl_cffi 起步（TLS 指纹强）
    3. 200 但 challenge.detect 命中 → 升级到 browser
    4. 4xx/5xx + AUTO_MODE_ESCALATION → 升一级（curl_cffi → browser）
    5. 成功后写回 domain_memory
    """
    started = time.time()
    requested_mode = mode
    cache_hit = False
    final_mode = mode
    domain = ""
    failed = False
    escalations: list[str] = []
    try:
        allow_private = _effective_allow_private(allow_private)
        verify_tls = _effective_verify_tls(verify_tls)
        _validate_url(url, allow_private=allow_private)
        domain = _domain_from_url(url)
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit,
                              allow_private=allow_private)
        pinned = _resolve_pinned(url, allow_private=allow_private) if not use_proxy else None
        variant = _cache_variant(mode, headers, cookies, use_proxy, verify_tls)
        if use_cache:
            req_type = {"requests": 1, "curl_cffi": 2, "browser": 3}.get(mode, 1)
            if mode == "auto":
                cached = _read_cache(url, 2, variant) or _read_cache(url, 1, variant) or _read_cache(url, 3, variant)
            else:
                cached = _read_cache(url, req_type, variant)
            if cached:
                cache_hit = True
                final_mode = "cache"
                return cached[:FETCH_MAX_LENGTH]

        proxy = _proxy_pool.get_proxy() if use_proxy else None
        parsed_headers, ua = _get_headers(headers)
        parsed_cookies = json.loads(cookies) if cookies else None

        # 显式模式：直接走该路径
        if mode == "browser":
            final_mode = "browser"
            html = _engine.fetch_with_browser(url)
            if save_cache:
                _write_cache(url, html, req_type=3, variant=variant)
            _record_domain_success(domain, "browser", "")
            return html[:FETCH_MAX_LENGTH]

        if mode == "curl_cffi":
            final_mode = "curl_cffi"
            impersonate = _get_matching_impersonate(ua)
            html = _engine.fetch_with_curl_cffi(url, parsed_headers, proxy, impersonate=impersonate,
                                                cookies=parsed_cookies, verify_tls=verify_tls,
                                                pinned=pinned)
            if save_cache:
                _write_cache(url, html, req_type=2, variant=variant)
            _record_domain_success(domain, "curl_cffi", impersonate)
            return html[:FETCH_MAX_LENGTH]

        if mode == "requests":
            final_mode = "requests"
            html = _engine.fetch_with_requests(url, parsed_headers, proxy, cookies=parsed_cookies,
                                               verify_tls=verify_tls, pinned=pinned)
            if save_cache:
                _write_cache(url, html, req_type=1, variant=variant)
            _record_domain_success(domain, "requests", "")
            return html[:FETCH_MAX_LENGTH]

        # ---------- auto 模式：智能升级 ----------
        # 1. 域名记忆：直接走最近成功模式
        memory = _lookup_domain_memory(domain)
        preferred = memory.get("preferred_mode") if memory and memory.get("fresh") else ""

        # 2. 决定起步模式
        if preferred in {"requests", "curl_cffi", "browser"}:
            tier = preferred
        elif HAS_CURL_CFFI:
            tier = "curl_cffi"
        else:
            tier = "requests"

        # 3. 升级链
        upgrade_chain = ["requests", "curl_cffi", "browser"]
        if tier == "curl_cffi" and not HAS_CURL_CFFI:
            tier = "requests"
        try_order = upgrade_chain[upgrade_chain.index(tier):] if AUTO_MODE_ESCALATION else [tier]
        if not HAS_CURL_CFFI and "curl_cffi" in try_order:
            try_order = [m for m in try_order if m != "curl_cffi"]
        if not HAS_PLAYWRIGHT and "browser" in try_order:
            try_order = [m for m in try_order if m != "browser"]
        if not try_order:
            try_order = ["requests"]

        last_exc: Exception | None = None
        for stage_index, stage in enumerate(try_order):
            try:
                final_mode = stage
                impersonate = ""
                if stage == "requests":
                    html = _engine.fetch_with_requests(url, parsed_headers, proxy,
                                                       cookies=parsed_cookies,
                                                       verify_tls=verify_tls, pinned=pinned)
                    req_type = 1
                elif stage == "curl_cffi":
                    impersonate = (memory or {}).get("impersonate") or _get_matching_impersonate(ua)
                    html = _engine.fetch_with_curl_cffi(url, parsed_headers, proxy,
                                                        impersonate=impersonate,
                                                        cookies=parsed_cookies,
                                                        verify_tls=verify_tls, pinned=pinned)
                    req_type = 2
                else:  # browser
                    html = _engine.fetch_with_browser(url)
                    req_type = 3

                # challenge 探测：HTTP 层成功但页面是挑战页 → 升级
                challenge_hit = ""
                if stage in ("requests", "curl_cffi") and DETECT_CHALLENGE_PAGES:
                    challenge_hit = _challenge_mod.detect_in_html(html, CHALLENGE_PATTERNS)
                if challenge_hit and stage_index + 1 < len(try_order):
                    escalations.append(f"{stage}->{try_order[stage_index + 1]}:challenge={challenge_hit}")
                    _record_domain_failure(domain, stage, challenge_hit)
                    continue  # 升级到下一级

                if save_cache:
                    _write_cache(url, html, req_type=req_type, variant=variant)
                _record_domain_success(domain, stage, impersonate)
                return html[:FETCH_MAX_LENGTH]
            except Exception as exc:
                last_exc = exc
                _record_domain_failure(domain, stage, "")
                if stage_index + 1 < len(try_order) and AUTO_MODE_ESCALATION:
                    escalations.append(f"{stage}->{try_order[stage_index + 1]}:error={type(exc).__name__}")
                    logger.info(f"模式 {stage} 失败，自动升级到 {try_order[stage_index + 1]}: {exc}")
                    continue
                raise
        raise last_exc or Exception("auto 模式所有路径均失败")
    except Exception as exc:
        failed = True
        _append_event({
            "event": "fetch",
            "success": False,
            "url": url,
            "domain": domain,
            "requested_mode": requested_mode,
            "mode": final_mode,
            "cache_hit": cache_hit,
            "escalations": escalations,
            "duration_ms": round((time.time() - started) * 1000),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        })
        raise
    finally:
        if not failed:
            _append_event({
                "event": "fetch",
                "success": True,
                "url": url,
                "domain": domain,
                "requested_mode": requested_mode,
                "mode": final_mode,
                "cache_hit": cache_hit,
                "escalations": escalations,
                "duration_ms": round((time.time() - started) * 1000),
            })


def _lookup_domain_memory(domain: str) -> dict | None:
    if not _domain_memory or not DOMAIN_MEMORY_ENABLED or not domain:
        return None
    try:
        return _domain_memory.lookup(domain)
    except Exception as exc:
        logger.warning(f"domain_memory.lookup 失败: {exc}", exc_info=True)
        return None


def _record_domain_success(domain: str, mode: str, impersonate: str) -> None:
    if not _domain_memory or not DOMAIN_MEMORY_ENABLED or not domain or mode == "cache":
        return
    try:
        _domain_memory.record_success(domain, mode, impersonate)
    except Exception as exc:
        logger.warning(f"domain_memory.record_success 失败: {exc}", exc_info=True)


def _record_domain_failure(domain: str, mode: str, challenge_hit: str) -> None:
    if not _domain_memory or not DOMAIN_MEMORY_ENABLED or not domain:
        return
    try:
        _domain_memory.record_failure(domain, mode, challenge_hit)
    except Exception as exc:
        logger.warning(f"domain_memory.record_failure 失败: {exc}", exc_info=True)


def _fetch_full_text(url: str, mode: str = "auto", use_cache: bool = True,
                     allow_private: bool = False) -> str:
    """Fetch text without normal page truncation. Intended for XML sitemaps."""
    old_limit = globals().get("FETCH_MAX_LENGTH", 80000)
    try:
        globals()["FETCH_MAX_LENGTH"] = max(int(old_limit), 20_000_000)
        return _smart_fetch(
            url,
            mode=mode,
            use_cache=use_cache,
            respect_robots=False,
            allow_private=allow_private,
        )
    finally:
        globals()["FETCH_MAX_LENGTH"] = old_limit

# ============ MCP 工具 ============

@mcp.tool()
def fetch_page(url: str, headers: str = "{}", use_cache: bool = True,
               mode: str = "auto", use_proxy: bool = False, cookies: str = "",
               respect_robots: bool = RESPECT_ROBOTS, rate_limit: float = 0.0,
               verify_tls: bool = VERIFY_TLS, allow_private: bool = False) -> str:
    """
    获取网页 HTML 内容。

    Args:
        url: 目标网页 URL
        headers: 请求头 JSON 字符串（可选）
        use_cache: 是否使用缓存
        mode: 请求模式 (auto/requests/curl_cffi/browser)
              auto: 自动选择（默认，优先 curl_cffi 绕过反爬）
              requests: 标准 HTTP 请求
              curl_cffi: TLS 指纹伪装（绕过 Cloudflare 等检测）
              browser: 浏览器渲染（处理 JS 动态页面）
        use_proxy: 是否使用代理池
        cookies: Cookies JSON 字符串，如 '{"session": "abc123"}'
        respect_robots: 是否遵守 robots.txt
        rate_limit: 本次请求使用的每域名 RPS，0 表示使用默认值
        verify_tls: 是否校验 TLS 证书
        allow_private: 是否允许访问内网/本机/保留地址，默认禁止

    Returns:
        网页 HTML 内容
    """
    try:
        return _smart_fetch(
            url, headers, use_cache, mode, use_proxy, cookies,
            respect_robots=respect_robots,
            rate_limit=rate_limit or None,
            verify_tls=verify_tls,
            allow_private=allow_private,
        )
    except Exception as e:
        return _error_result(str(e), "fetch_failed",
            "尝试 mode=browser 或 use_proxy=True")

@mcp.tool()
def fetch_best_page(url: str, modes: str = "curl_cffi,requests,browser",
                    target_selector: str = "", headers: str = "{}",
                    use_cache: bool = False, use_proxy: bool = False,
                    cookies: str = "", respect_robots: bool = RESPECT_ROBOTS,
                    rate_limit: float = 0.0, verify_tls: bool = VERIFY_TLS,
                    allow_private: bool = False, return_html: bool = False,
                    max_html_length: int = 5000) -> str:
    """
    多模式抓取同一页面并按响应质量打分，选择最佳 HTML。

    用于避免 auto 模式简单升级导致的倒退：例如 curl_cffi 已拿到 JSON-LD，
    但 browser 反而命中 challenge。
    """
    try:
        mode_list = [m.strip() for m in (modes or "").split(",") if m.strip()]
        if not mode_list:
            mode_list = ["curl_cffi", "requests", "browser"]
        seen_modes: set[str] = set()
        candidates = []
        for mode in mode_list:
            if mode in seen_modes:
                continue
            seen_modes.add(mode)
            if mode == "curl_cffi" and not HAS_CURL_CFFI:
                candidates.append({"mode": mode, "ok": False, "error": "curl_cffi not installed", "score": -999})
                continue
            if mode == "browser" and not HAS_PLAYWRIGHT:
                candidates.append({"mode": mode, "ok": False, "error": "playwright not installed", "score": -999})
                continue
            try:
                html = _smart_fetch(
                    url,
                    headers=headers,
                    use_cache=use_cache,
                    mode=mode,
                    use_proxy=use_proxy,
                    cookies=cookies,
                    respect_robots=respect_robots,
                    rate_limit=rate_limit or None,
                    verify_tls=verify_tls,
                    save_cache=False,
                    allow_private=allow_private,
                )
                scored = _score_fetch_candidate(html, target_selector=target_selector)
                item = {
                    "mode": mode,
                    "ok": True,
                    **{k: v for k, v in scored.items() if k != "diagnostics"},
                    "diagnostics": scored["diagnostics"],
                }
                if return_html:
                    item["html"] = html[:max(0, int(max_html_length or 0))]
                candidates.append(item)
            except Exception as exc:
                candidates.append({
                    "mode": mode,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "score": -999,
                    "length": 0,
                })
        best = max(candidates, key=lambda c: c.get("score", -999)) if candidates else {}
        recommendations = []
        if best and best.get("ok"):
            recommendations.append({
                "action": "use_mode",
                "mode": best.get("mode"),
                "why": "highest response score across requested modes",
            })
        if any(c.get("ok") and c.get("diagnostics", {}).get("challenge", {}).get("detected") for c in candidates):
            recommendations.append({
                "action": "avoid_challenge_candidate",
                "why": "one or more modes returned challenge-like content",
            })
        payload = _v5_envelope(
            bool(best and best.get("ok")),
            data={"url": url, "best_mode": best.get("mode"), "best_score": best.get("score"), "best": best, "candidates": candidates},
            recommendations=recommendations,
            best_mode=best.get("mode"),
            candidates=candidates,
        )
        return _success_result(payload)
    except Exception as e:
        return _error_result(str(e), "fetch_best_failed", "检查 URL、modes 或访问策略")

@mcp.tool()
def fetch_post(url: str, data: str = "{}", content_type: str = "application/json",
               headers: str = "{}", use_proxy: bool = False,
               respect_robots: bool = RESPECT_ROBOTS, rate_limit: float = 0.0,
               verify_tls: bool = VERIFY_TLS, allow_private: bool = False) -> str:
    """
    发送 POST 请求（支持 JSON API、表单提交等）。

    Args:
        url: 目标 URL
        data: 请求体（JSON 字符串或表单数据）
        content_type: 内容类型 (application/json, application/x-www-form-urlencoded, multipart/form-data)
        headers: 请求头 JSON 字符串
        use_proxy: 是否使用代理

    Returns:
        响应内容
    """
    try:
        verify_tls = _effective_verify_tls(verify_tls)
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        proxy = _proxy_pool.get_proxy() if use_proxy else None
        parsed_headers = json.loads(headers) if isinstance(headers, str) else headers
        return _engine.post_with_requests(url, data, content_type, parsed_headers, proxy, verify_tls=verify_tls)
    except Exception as e:
        return _error_result(str(e), "post_failed", "检查 URL 和请求体格式")

@mcp.tool()
def fetch_json(url: str, method: str = "GET", body: str = "",
               headers: str = "{}", json_path: str = "",
               respect_robots: bool = RESPECT_ROBOTS, rate_limit: float = 0.0,
               verify_tls: bool = VERIFY_TLS, allow_private: bool = False) -> str:
    """
    获取 JSON API 数据，支持 JSONPath 提取。

    Args:
        url: API URL
        method: 请求方法 (GET/POST)
        body: POST 请求体（JSON 字符串）
        headers: 请求头 JSON 字符串
        json_path: JSONPath 表达式（简单的点号路径，如 "data.items.0.title"）

    Returns:
        JSON 数据
    """
    try:
        verify_tls = _effective_verify_tls(verify_tls)
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        parsed_headers, _ = _get_headers(headers)
        parsed_headers["Accept"] = "application/json"
        if method.upper() == "POST":
            resp_text = _engine.post_with_requests(url, body, "application/json", parsed_headers,
                                                   verify_tls=verify_tls)
        else:
            resp_text = _engine.fetch_with_requests(url, parsed_headers, verify_tls=verify_tls)
        data = json.loads(resp_text)
        if json_path:
            for key in json_path.split("."):
                if isinstance(data, list):
                    data = data[int(key)]
                else:
                    data = data[key]
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "json_fetch_failed", "检查 URL 和 JSON 格式")

@mcp.tool()
def parse_html(html: str, selector: str) -> str:
    """
    使用 CSS 选择器从 HTML 中提取数据。

    Args:
        html: HTML 内容
        selector: CSS 选择器，如 ".title" 或 "#content li"

    Returns:
        提取到的文本列表 JSON
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(selector)
        results = [elem.get_text(strip=True) for elem in elements]
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "parse_failed", "检查 CSS 选择器语法")

@mcp.tool()
def extract_links(html: str, base_url: str = "") -> str:
    """
    从 HTML 中提取所有链接。

    Args:
        html: HTML 内容
        base_url: 基础 URL（用于补全相对链接）

    Returns:
        链接列表 JSON，每个元素包含 url 和 text
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if base_url and not href.startswith(("http://", "https://")):
                href = urljoin(base_url, href)
            links.append({"url": href, "text": text})
        return json.dumps(links[:200], ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "extract_failed")

@mcp.tool()
def extract_text(html: str, selector: str = "") -> str:
    """
    从 HTML 中提取纯文本（自动去除 script/style）。

    Args:
        html: HTML 内容
        selector: CSS 选择器（可选，为空则提取全部）

    Returns:
        纯文本内容
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        if selector:
            elements = soup.select(selector)
            return "\n".join(elem.get_text(separator=" ", strip=True) for elem in elements)
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return _error_result(str(e), "extract_failed")

@mcp.tool()
def extract_structured_data(html: str) -> str:
    """
    从 HTML 中提取结构化数据（JSON-LD、Open Graph、Twitter Card、Schema.org）。

    Args:
        html: HTML 内容

    Returns:
        结构化数据 JSON，包含 json_ld、open_graph、twitter_card 字段
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        result = {"json_ld": [], "open_graph": {}, "twitter_card": {}, "meta": {}}

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld_data = json.loads(script.string)
                result["json_ld"].append(ld_data)
            except Exception:
                pass

        # Open Graph
        for meta in soup.find_all("meta", property=re.compile(r"^og:")):
            result["open_graph"][meta["property"]] = meta.get("content", "")

        # Twitter Card
        for meta in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
            result["twitter_card"][meta["name"]] = meta.get("content", "")

        # 基础 Meta
        for name in ["description", "keywords", "author"]:
            meta = soup.find("meta", attrs={"name": name})
            if meta:
                result["meta"][name] = meta.get("content", "")

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "extract_failed")

@mcp.tool()
def extract_initial_state(html: str, path: str = "", base_url: str = "",
                          output_format: str = "raw", max_depth: int = 4,
                          include_filtered: bool = False) -> str:
    """
    从 HTML 脚本中提取前端初始状态 JSON，可按路径读取业务结构。

    Args:
        html: HTML 内容
        path: 点号路径，如 navigation.multiBrandMenu[0].mainMenu；为空时返回候选来源
        base_url: 用于补全菜单 URL
        output_format: raw/tree/dict，tree 会输出过滤报告，dict 输出 title->url 字典
        max_depth: 菜单树最大深度
        include_filtered: 是否在 tree 中保留 hidden/contentPage/externalLink 等被过滤项

    Returns:
        JSON：包含命中来源、候选路径、提取值或菜单树与过滤报告
    """
    try:
        sources = _extract_initial_state_sources(html)
        candidates = _candidate_menu_paths(sources)
        result: dict[str, Any] = {
            "source_count": len(sources),
            "sources": [
                {
                    "name": source["name"],
                    "script_index": source["script_index"],
                    "path": source["path"],
                    "summary": source["summary"],
                }
                for source in sources
            ],
            "menu_candidates": candidates,
        }
        if not path:
            result["selected"] = candidates[0] if candidates else None
            result["explanation"] = "未提供 path；按 multiBrandMenu/mainMenu/navigation 等信号排序返回候选。"
            return _success_result(result)

        resolved = _resolve_initial_state_path(sources, path)
        if not resolved:
            result["matched"] = False
            result["path"] = path
            result["explanation"] = "指定路径未在已识别的初始状态脚本中命中。"
            return _success_result(result)

        value = resolved["value"]
        result.update({
            "matched": True,
            "path": path,
            "source": resolved["source"],
            "script_index": resolved["script_index"],
            "summary": _value_summary(value),
        })
        if output_format == "tree":
            tree = _menu_to_tree(value, base_url=base_url, max_depth=max_depth, include_filtered=include_filtered)
            result.update(tree)
        elif output_format == "dict":
            tree = _menu_to_tree(value, base_url=base_url, max_depth=max_depth, include_filtered=include_filtered)
            result["items"] = _tree_to_title_dict(tree["items"])
            result["count"] = tree["count"]
            result["filter_report"] = tree["filter_report"]
            result["filtered_samples"] = tree["filtered_samples"]
        else:
            result["value"] = value
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "initial_state_extract_failed")

@mcp.tool()
def compare_menu_sources(html: str, base_url: str = "", paths: str = "",
                         max_depth: int = 4, output_format: str = "summary") -> str:
    """
    比较 HTML 初始状态中的多个菜单来源，解释候选差异与推荐来源。

    Args:
        html: HTML 内容
        base_url: 用于补全菜单 URL
        paths: 可选 JSON 数组或逗号分隔路径；为空时自动枚举 menu/navigation 候选
        max_depth: 菜单树最大深度
        output_format: summary/tree/dict

    Returns:
        JSON：候选菜单路径、计数、过滤报告、推荐来源和解释
    """
    try:
        sources = _extract_initial_state_sources(html)
        auto_candidates = _candidate_menu_paths(sources, max_candidates=50)
        requested_paths: list[str] = []
        if paths:
            try:
                parsed_paths = json.loads(paths)
                if isinstance(parsed_paths, list):
                    requested_paths = [str(item) for item in parsed_paths]
            except json.JSONDecodeError:
                requested_paths = [item.strip() for item in paths.split(",") if item.strip()]
        if not requested_paths:
            requested_paths = [item["path"] for item in auto_candidates]

        comparisons = []
        for path in requested_paths:
            resolved = _resolve_initial_state_path(sources, path)
            if not resolved:
                comparisons.append({"path": path, "matched": False, "reason": "path_not_found"})
                continue
            tree = _menu_to_tree(resolved["value"], base_url=base_url, max_depth=max_depth)
            item = {
                "path": path,
                "matched": True,
                "source": resolved["source"],
                "script_index": resolved["script_index"],
                "count": tree["count"],
                "top_count": len(tree["items"]),
                "filter_report": tree["filter_report"],
                "filtered_samples": tree["filtered_samples"],
                "score": _score_menu_source(path, resolved["value"]) + min(tree["count"], 30),
            }
            if output_format == "tree":
                item["items"] = tree["items"]
            elif output_format == "dict":
                item["items"] = _tree_to_title_dict(tree["items"])
            comparisons.append(item)

        matched = [item for item in comparisons if item.get("matched")]
        recommended = max(matched, key=lambda item: item.get("score", 0), default=None)
        result = {
            "source_count": len(sources),
            "candidate_count": len(auto_candidates),
            "auto_candidates": auto_candidates[:20],
            "comparisons": comparisons,
            "recommended": recommended,
            "explanation": (
                "优先选择同时包含 multiBrandMenu/mainMenu/navigation 信号、节点数量较多、过滤后仍有有效 URL 的菜单来源。"
                if recommended else
                "未找到可解析的菜单来源；建议先用 extract_initial_state 不带 path 查看脚本来源。"
            ),
        }
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "menu_compare_failed")

@mcp.tool()
def fetch_page_browser(url: str, wait_selector: str = "",
                       render_time: float = 5.0,
                       wait_until: str = "domcontentloaded",
                       scroll_count: int = 0, scroll_delay: float = 1.0,
                       use_cache: bool = True,
                       respect_robots: bool = RESPECT_ROBOTS,
                       rate_limit: float = 0.0,
                       allow_private: bool = False) -> str:
    """
    使用真实浏览器渲染获取网页（处理 JS 动态加载页面）。

    适用于：百度热榜、React/Vue SPA、无限滚动页面等。
    浏览器会模拟真实用户行为，自动注入反检测脚本。

    Args:
        url: 目标网页 URL
        wait_selector: 等待特定 CSS 元素出现后再获取（如 ".hot-item"）
        render_time: 额外渲染等待时间（秒），默认 5 秒
        wait_until: 页面加载策略 (domcontentloaded/load/networkidle)
        scroll_count: 自动滚动次数（用于无限滚动页面加载更多内容）
        scroll_delay: 每次滚动后等待时间（秒）

    Returns:
        渲染后的完整 HTML
    """
    try:
        _validate_url(url, allow_private=allow_private)
        variant = _cache_variant(wait_selector, render_time, wait_until, scroll_count, scroll_delay)
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        cached = _read_cache(url, req_type=3, variant=variant) if use_cache else None
        if cached:
            return cached
        html = _engine.fetch_with_browser(url, wait_until, render_time, wait_selector, scroll_count, scroll_delay)
        _write_cache(url, html, req_type=3, variant=variant)
        return html
    except Exception as e:
        return _error_result(str(e), "browser_failed", "确保 Playwright 已安装: playwright install chromium")

@mcp.tool()
def scroll_and_load(url: str, scroll_times: int = 5, wait_selector: str = "",
                    scroll_delay: float = 2.0,
                    respect_robots: bool = RESPECT_ROBOTS,
                    rate_limit: float = 0.0,
                    allow_private: bool = False) -> str:
    """
    无限滚动加载页面内容（适用于 Twitter/Instagram/商品列表等）。

    Args:
        url: 目标 URL
        scroll_times: 滚动次数（默认 5 次）
        wait_selector: 等待特定元素出现
        scroll_delay: 每次滚动后等待秒数

    Returns:
        滚动加载后的完整 HTML
    """
    try:
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        return _engine.fetch_with_browser(url, "domcontentloaded", 2, wait_selector, scroll_times, scroll_delay)
    except Exception as e:
        return _error_result(str(e), "scroll_failed")

@mcp.tool()
def take_screenshot(url: str, full_page: bool = True, wait_selector: str = "",
                    respect_robots: bool = RESPECT_ROBOTS,
                    rate_limit: float = 0.0,
                    allow_private: bool = False) -> str:
    """
    对网页截图并保存到 output 目录。

    Args:
        url: 目标 URL
        full_page: 是否截取完整页面（含滚动区域）
        wait_selector: 等待特定元素出现

    Returns:
        截图文件路径
    """
    try:
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        return _engine.take_screenshot(url, full_page, wait_selector)
    except Exception as e:
        return _error_result(str(e), "screenshot_failed")

@mcp.tool()
def crawl_list(url: str, link_selector: str, base_url: str = "",
               max_links: int = 100, mode: str = "auto",
               use_cache: bool = True,
               script_fallback: bool = True,
               respect_robots: bool = RESPECT_ROBOTS,
               allow_private: bool = False) -> str:
    """
    爬取列表页，提取所有商品/文章链接。

    Args:
        url: 列表页 URL
        link_selector: 链接的 CSS 选择器（如 ".product a", ".post-title"）
        base_url: 基础 URL（补全相对链接，如 "https://example.com"）
        max_links: 最大链接数
        mode: 请求模式 (auto/requests/curl_cffi/browser)
        script_fallback: CSS 选择器 0 命中时，是否扫描脚本 JSON 中的 URL 并返回诊断

    Returns:
        JSON 格式链接列表，每个元素包含 url 和 text
    """
    try:
        html = _smart_fetch(url, mode=mode, use_cache=use_cache,
                            respect_robots=respect_robots, allow_private=allow_private)
        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(link_selector)
        links = []
        for elem in elements:
            href = elem.get("href", "")
            if not href:
                a_tag = elem.find("a", href=True)
                if a_tag:
                    href = a_tag["href"]
            if href:
                if base_url and not href.startswith(("http://", "https://")):
                    href = urljoin(base_url, href)
                links.append({"url": href, "text": elem.get_text(strip=True)})
        result = {"source": url, "count": len(links[:max_links]), "links": links[:max_links]}
        if not links:
            diagnostics = _diagnose_zero_link_result(html, url, link_selector, base_url, max_links)
            result["diagnostics"] = diagnostics
            result["explanation"] = (
                "CSS 选择器没有命中链接；诊断已检查 DOM 链接数、脚本内 URL、challenge 和可能截断信号。"
            )
            if script_fallback and diagnostics["script_url_samples"]:
                fallback_links = diagnostics["script_url_samples"][:max_links]
                result["count"] = len(fallback_links)
                result["links"] = fallback_links
                result["fallback_used"] = "script_json_url_scan"
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "crawl_failed")

@mcp.tool()
def crawl_product(url: str, fields: str, mode: str = "auto",
                  use_cache: bool = True,
                  respect_robots: bool = RESPECT_ROBOTS,
                  allow_private: bool = False) -> str:
    """
    爬取产品/文章详情页，按字段提取数据。

    Args:
        url: 详情页 URL
        fields: 字段定义 JSON，如 {"title": "h1", "price": ".price", "image": "img.product@src"}
               用 @属性名 提取属性值，如 "img@src" 提取图片链接
               支持多个元素：选择器匹配多个时返回数组
        mode: 请求模式 (auto/requests/curl_cffi/browser)

    Returns:
        提取的数据 JSON，包含 url 和各字段值
    """
    try:
        html = _smart_fetch(url, mode=mode, use_cache=use_cache,
                            respect_robots=respect_robots, allow_private=allow_private)
        soup = BeautifulSoup(html, "html.parser")
        field_defs = json.loads(fields)
        result = {"url": url}
        for name, selector in field_defs.items():
            if "@" in selector:
                css, attr = selector.rsplit("@", 1)
                elem = soup.select_one(css)
                result[name] = elem.get(attr, "") if elem else ""
            else:
                elems = soup.select(selector)
                if len(elems) == 1:
                    result[name] = elems[0].get_text(strip=True)
                elif len(elems) > 1:
                    result[name] = [e.get_text(strip=True) for e in elems]
                else:
                    result[name] = ""
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "crawl_failed")

@mcp.tool()
def scout_page(url: str, goal: str = "product_list", mode: str = "auto",
               use_cache: bool = True, target_selector: str = "",
               max_candidates: int = 8,
               respect_robots: bool = RESPECT_ROBOTS,
               allow_private: bool = False) -> str:
    """
    面向 Agent 的页面侦察入口：抓取页面并汇总访问诊断、初始状态、菜单候选、链接/字段候选和下一步建议。
    """
    try:
        scout = _scout_page_data(
            url=url,
            goal=goal,
            mode=mode,
            use_cache=use_cache,
            target_selector=target_selector,
            max_candidates=max_candidates,
            respect_robots=respect_robots,
            allow_private=allow_private,
        )
        return _success_result(_v5_envelope(
            scout.get("ok", False),
            data={
                "url": scout.get("url"),
                "goal": scout.get("goal"),
                "page": scout.get("page"),
                "initial_state": scout.get("initial_state"),
                "menu_candidates": scout.get("menu_candidates"),
                "link_candidates": scout.get("link_candidates"),
                "field_candidates": scout.get("field_candidates"),
                "script_url_candidates": scout.get("script_url_candidates"),
                "api_hints": scout.get("api_hints"),
                "recommended_plan": scout.get("recommended_plan"),
            },
            diagnostics={"access": scout.get("access"), "dom_link_sample": scout.get("dom_link_sample")},
            recommendations=scout.get("recommendations", []),
            **_v5_compat(scout),
        ))
    except Exception as e:
        return _error_result(str(e), "page_scout_failed")

@mcp.tool()
def draft_collection_plan(url: str, goal: str = "采集商品标题、价格和图片",
                          fields: str = "", mode: str = "auto",
                          max_items: int = 20, output: str = "json",
                          output_format: str = "records",
                          use_cache: bool = True,
                          respect_robots: bool = RESPECT_ROBOTS,
                          allow_private: bool = False) -> str:
    """
    根据页面侦察结果起草 Agent 可审阅的采集计划。
    推荐主链：scout_page -> draft_collection_plan -> validate_collection_plan -> execute_collection_plan。
    """
    try:
        scout = _scout_page_data(
            url=url,
            goal=goal,
            mode=mode,
            use_cache=use_cache,
            max_candidates=8,
            respect_robots=respect_robots,
            allow_private=allow_private,
        )
        draft = _draft_plan_from_scout(
            scout=scout,
            goal=goal,
            fields=fields,
            max_items=max_items,
            output=output,
        )
        draft["plan"]["output_format"] = output_format
        draft["validation_hint"] = "下一步调用 validate_collection_plan(plan, sample=true)。"
        return _success_result(_v5_envelope(
            draft.get("recommendation") == "ready_to_validate",
            data={
                "plan": draft.get("plan"),
                "confidence": draft.get("confidence"),
                "recommendation": draft.get("recommendation"),
                "reasons": draft.get("reasons"),
                "scout_summary": draft.get("scout_summary"),
            },
            diagnostics={"scout_summary": draft.get("scout_summary")},
            recommendations=[{"type": "validate_next", "action": "validate_collection_plan"}],
            **_v5_compat(draft),
        ))
    except Exception as e:
        return _error_result(str(e), "collection_plan_draft_failed")

@mcp.tool()
def validate_collection_plan(plan: str, sample: bool = True,
                             allow_private: bool = False) -> str:
    """
    校验 Agent 生成的采集计划，并返回将要执行的 pipeline 与可选样本命中情况。
    """
    try:
        plan_obj = _json_obj(plan, {})
        shape = _validate_collection_plan_shape(plan_obj)
        pipeline = _build_collection_pipeline(plan_obj, validate_only=True)
        result: dict[str, Any] = {
            "ok": shape["ok"],
            "errors": shape["errors"],
            "warnings": shape["warnings"],
            "pipeline": pipeline,
        }
        if sample and shape["ok"]:
            start_url = plan_obj.get("start_url") or plan_obj.get("url") or plan_obj.get("list_url")
            mode = plan_obj.get("mode", "auto")
            html = _smart_fetch(
                start_url,
                mode=mode,
                use_cache=bool(plan_obj.get("use_cache", True)),
                respect_robots=bool(plan_obj.get("respect_robots", RESPECT_ROBOTS)),
                allow_private=allow_private or bool(plan_obj.get("allow_private", False)),
            )
            selector = plan_obj.get("list_selector") or plan_obj.get("selector") or plan_obj.get("item_link")
            if selector:
                links = _extract_links_from_html(
                    html,
                    start_url,
                    selector,
                    plan_obj.get("base_url", start_url),
                    int(plan_obj.get("max_items", 20)),
                )
                result["sample"] = {
                    "list_selector": selector,
                    "links_count": len(links),
                    "links": links[:5],
                    "diagnostics": (
                        _diagnose_zero_link_result(
                            html,
                            start_url,
                            selector,
                            plan_obj.get("base_url", start_url),
                            int(plan_obj.get("max_items", 20)),
                        ) if not links else {}
                    ),
                }
        return _success_result(_v5_envelope(
            shape["ok"],
            data={"pipeline": pipeline, "sample": result.get("sample")},
            diagnostics={"errors": shape["errors"], "warnings": shape["warnings"]},
            recommendations=(
                [{"type": "execute_next", "action": "execute_collection_plan"}]
                if shape["ok"] else
                [{"type": "fix_plan", "errors": shape["errors"]}]
            ),
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "collection_plan_validate_failed")

@mcp.tool()
def execute_collection_plan(plan: str, allow_private: bool = False) -> str:
    """
    执行 Agent 生成的采集计划。计划会先转换为现有 Pipeline DSL，再复用统一执行器。
    """
    try:
        plan_obj = _json_obj(plan, {})
        shape = _validate_collection_plan_shape(plan_obj)
        if not shape["ok"]:
            return _success_result({
                "success": False,
                "ok": False,
                "errors": shape["errors"],
                "warnings": shape["warnings"],
            })
        pipeline = _build_collection_pipeline(plan_obj, validate_only=False)
        result = _run_pipeline_spec(pipeline, allow_private=allow_private or bool(plan_obj.get("allow_private", False)))
        result["pipeline"] = pipeline
        result["warnings"] = shape["warnings"]
        return _success_result(_v5_envelope(
            bool(result.get("success")),
            data={
                "links_count": result.get("links_count"),
                "records_count": result.get("records_count"),
                "sample": result.get("sample"),
                "formatted_sample": result.get("formatted_sample"),
                "output_format": result.get("output_format"),
                "artifacts": result.get("artifacts"),
                "db_results": result.get("db_results"),
                "pipeline": pipeline,
            },
            diagnostics={"steps": result.get("steps"), "warnings": shape["warnings"]},
            recommendations=[],
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "collection_plan_execute_failed")

def _xml_child_text(elem, name: str) -> str:
    for child in list(elem):
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    return ""

@mcp.tool()
def parse_sitemap(url: str, allow_private: bool = False, max_depth: int = 5,
                  max_urls: int = 50000) -> str:
    """
    解析网站 Sitemap（sitemap.xml），提取所有 URL。

    Args:
        url: sitemap.xml 的 URL（如 https://example.com/sitemap.xml）
        max_depth: sitemap index 最大展开深度，防止恶意递归
        max_urls: 最大返回 URL 数量，防止超大 sitemap 拖垮进程

    Returns:
        URL 列表 JSON，包含 url、lastmod、changefreq、priority
    """
    try:
        max_depth = max(0, min(int(max_depth), 10))
        max_urls = max(1, min(int(max_urls), 200000))
        headers, _ = _get_headers()
        urls = []
        queue: list[tuple[str, int]] = [(url, 0)]
        seen_sitemaps: set[str] = set()

        while queue and len(urls) < max_urls:
            current_url, depth = queue.pop(0)
            if current_url in seen_sitemaps:
                continue
            seen_sitemaps.add(current_url)
            _apply_request_policy(current_url, respect_robots=False, allow_private=allow_private)
            resp = _requests_lib.get(current_url, headers=headers, timeout=REQUEST_TIMEOUT, verify=VERIFY_TLS)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            root_name = root.tag.rsplit("}", 1)[-1]

            if root_name == "sitemapindex":
                if depth >= max_depth:
                    continue
                for sitemap in list(root):
                    if sitemap.tag.rsplit("}", 1)[-1] != "sitemap":
                        continue
                    loc = _xml_child_text(sitemap, "loc")
                    if loc:
                        queue.append((loc, depth + 1))
                continue

            for url_elem in list(root):
                if url_elem.tag.rsplit("}", 1)[-1] != "url":
                    continue
                entry = {"url": _xml_child_text(url_elem, "loc")}
                for tag in ["lastmod", "changefreq", "priority"]:
                    value = _xml_child_text(url_elem, tag)
                    if value:
                        entry[tag] = value
                if entry.get("url"):
                    urls.append(entry)
                    if len(urls) >= max_urls:
                        break
        return json.dumps(urls, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "sitemap_failed", "检查 sitemap.xml URL 是否正确")

# ============ Spider 兼容工具 ============

@mcp.tool()
def spider_fetch_page(url: str, use_browser: bool = False, headers: str = "{}",
                      cache: bool = True, save_cache: bool = True,
                      render_time: float = 0.0, await_condition: str = "",
                      use_proxy: bool = False,
                      request_type: str = "auto",
                      respect_robots: bool = RESPECT_ROBOTS,
                      allow_private: bool = False) -> str:
    """
    兼容 D:/pyproject/spider优化缓存快照 的页面采集工具。

    支持 HTTP/curl_cffi/浏览器渲染、缓存、等待元素、代理池与 robots/限速策略。
    """
    try:
        if use_browser:
            _validate_url(url, allow_private=allow_private)
            variant = _cache_variant("spider_browser", await_condition, render_time, headers, use_proxy)
            _apply_request_policy(url, respect_robots=respect_robots, allow_private=allow_private)
            cached = _read_cache(url, 3, variant) if cache else None
            if cached:
                return cached[:FETCH_MAX_LENGTH]
            html = _engine.fetch_with_browser(
                url,
                wait_until="domcontentloaded",
                render_time=render_time,
                wait_selector=await_condition,
            )
            if save_cache:
                _write_cache(url, html, 3, variant)
            return html[:FETCH_MAX_LENGTH]

        mode = "curl_cffi" if request_type == "curl_cffi" else request_type
        if mode not in {"auto", "requests", "curl_cffi"}:
            mode = "auto"
        html = _smart_fetch(
            url,
            headers=headers,
            use_cache=cache,
            mode=mode,
            use_proxy=use_proxy,
            respect_robots=respect_robots,
            save_cache=save_cache,
            allow_private=allow_private,
        )
        return html[:FETCH_MAX_LENGTH]
    except Exception as e:
        return _error_result(str(e), "spider_fetch_failed")

@mcp.tool()
def spider_parse_html(html: str, selector: str) -> str:
    """Spider 兼容：使用 CSS 选择器提取文本列表。"""
    return parse_html(html, selector)

@mcp.tool()
def spider_extract_links(html: str, base_url: str = "") -> str:
    """Spider 兼容：提取链接并补全相对 URL。"""
    return extract_links(html, base_url)

@mcp.tool()
def spider_extract_text(html: str, selector: str = "") -> str:
    """Spider 兼容：提取纯文本。"""
    return extract_text(html, selector)

@mcp.tool()
def spider_crawl_list(url: str, link_selector: str, base_url: str = "",
                      use_browser: bool = False, cache: bool = True,
                      max_links: int = 200,
                      respect_robots: bool = RESPECT_ROBOTS,
                      allow_private: bool = False) -> str:
    """Spider 兼容：抓取列表页并提取商品/文章链接。"""
    mode = "browser" if use_browser else "auto"
    return crawl_list(
        url=url,
        link_selector=link_selector,
        base_url=base_url,
        max_links=max_links,
        mode=mode,
        use_cache=cache,
        respect_robots=respect_robots,
        allow_private=allow_private,
    )

@mcp.tool()
def spider_crawl_product(url: str, fields: str, use_browser: bool = False,
                         cache: bool = True,
                         respect_robots: bool = RESPECT_ROBOTS,
                         allow_private: bool = False) -> str:
    """Spider 兼容：抓取详情页并按字段定义提取结构化数据。"""
    mode = "browser" if use_browser else "auto"
    return crawl_product(
        url=url,
        fields=fields,
        mode=mode,
        use_cache=cache,
        respect_robots=respect_robots,
        allow_private=allow_private,
    )

@mcp.tool()
def spider_save_to_db(data: str, db_name: str = "mcp_spider") -> str:
    """Spider 兼容：保存商品数据到 goods 表。"""
    return save_to_db(data=data, db_name=db_name, table="goods")

@mcp.tool()
def spider_query_db(db_name: str = "mcp_spider", limit: int = 10, offset: int = 0,
                    where: str = "") -> str:
    """Spider 兼容：查询 goods 表。where 使用安全 JSON 条件。"""
    return query_db(db_name=db_name, table="goods", limit=limit, offset=offset, where=where)

@mcp.tool()
def register_table_schema(db_name: str, table: str, schema: str) -> str:
    """
    注册企业模式表结构。注册后 save_to_db/save_batch_to_db 会自动校验字段、唯一键和索引。

    schema 示例：
    {
      "columns": {"url": "TEXT", "title": "TEXT", "price": "REAL"},
      "required": ["url", "title"],
      "unique": ["url"],
      "indexes": ["title"],
      "strict": true
    }
    """
    try:
        table_schema = _load_schema(db_name, table, schema)
        path = _schema_file(db_name, table)
        path.write_text(json.dumps(table_schema, ensure_ascii=False, indent=2), encoding="utf-8")

        db_path = DB_DIR / f"{db_name}.db"
        with _db_pool.connection(db_path) as conn:
            cursor = conn.cursor()
            _ensure_table(cursor, table, {}, table_schema)
            _ensure_schema_indexes(cursor, table, table_schema)
            conn.commit()
        return f"已注册 schema: {path}"
    except Exception as e:
        return _error_result(str(e), "schema_register_failed")

@mcp.tool()
def get_table_schema(db_name: str, table: str) -> str:
    """查看已注册的企业模式表结构。"""
    try:
        path = _schema_file(db_name, table)
        if not path.exists():
            return _error_result("未注册 schema，当前表会使用轻量动态模式", "schema_not_found")
        return path.read_text(encoding="utf-8")
    except Exception as e:
        return _error_result(str(e), "schema_read_failed")

@mcp.tool()
def save_data(data: str, filename: str) -> str:
    """
    保存数据到文件（output 目录下）。

    Args:
        data: 要保存的数据
        filename: 文件名（如 result.json, data.txt, output.csv）

    Returns:
        保存结果
    """
    try:
        raw_path = Path(filename)
        if raw_path.is_absolute() or raw_path.name != filename:
            return _error_result("文件名不能包含路径", "invalid_filename", "只允许 output 目录下的简单文件名")
        safe_name = Path(filename).name
        if not re.match(r"^[a-zA-Z0-9._-]+$", safe_name):
            return _error_result("文件名包含非法字符", "invalid_filename", "只允许字母数字.-_")
        if len(safe_name) > 200:
            return _error_result("文件名过长", "invalid_filename")
        filepath = OUTPUT_DIR / safe_name
        filepath.write_text(data, encoding="utf-8")
        return f"已保存到: {filepath}"
    except Exception as e:
        return _error_result(str(e), "save_failed")

@mcp.tool()
def save_to_db(data: str, db_name: str = "crawler_data", table: str = "products",
               schema: str = "") -> str:
    """
    保存数据到 SQLite 数据库（自动建表、自动去重、自动适配字段）。

    Args:
        data: 数据 JSON，支持任意字段。如 {"url": "...", "title": "...", "price": 99}
        db_name: 数据库名称
        table: 表名（默认 products，支持自定义表名）
        schema: 可选表结构 JSON；为空时自动读取 schemas/{db}.{table}.json

    Returns:
        保存结果
    """
    try:
        _validate_identifier(db_name, "数据库名")
        _validate_identifier(table, "表名")

        data_dict = json.loads(data)
        if not isinstance(data_dict, dict):
            return _error_result("data 必须是 JSON 对象", "invalid_input")
        table_schema = _load_schema(db_name, table, schema)
        record = _prepare_record(data_dict, table_schema)

        db_path = DB_DIR / f"{db_name}.db"
        with _db_pool.connection(db_path) as conn:
            cursor = conn.cursor()
            _ensure_table(cursor, table, record, table_schema)
            _ensure_schema_indexes(cursor, table, table_schema)
            inserted, sole_id = _insert_record(cursor, table, record, table_schema)
            conn.commit()
            if not inserted:
                return f"数据已存在（sole_id: {sole_id[:10]}...），跳过"
        return f"已保存到: {db_path} ({table})，sole_id: {sole_id[:10]}..."
    except Exception as e:
        return _error_result(str(e), "db_save_failed")

@mcp.tool()
def save_batch_to_db(data_list: str, db_name: str = "crawler_data", table: str = "products",
                     schema: str = "", atomic: bool = True) -> str:
    """
    批量保存数据到 SQLite（单次事务插入，比逐条保存快 50-100 倍）。

    Args:
        data_list: 数据 JSON 数组，如 [{"url": "...", "title": "..."}, {...}]
        db_name: 数据库名称
        table: 表名
        schema: 可选表结构 JSON；为空时自动读取 schemas/{db}.{table}.json
        atomic: 是否使用原子事务。默认 True，任意错误会回滚全部写入；False 时保留成功记录并统计错误。

    Returns:
        保存结果统计
    """
    try:
        _validate_identifier(db_name, "数据库名")
        _validate_identifier(table, "表名")

        items = json.loads(data_list)
        if not isinstance(items, list):
            return _error_result("data_list 必须是 JSON 数组", "invalid_input")
        table_schema = _load_schema(db_name, table, schema)
        prepared_items = []
        for item in items:
            if not isinstance(item, dict):
                return _error_result("data_list 中每一项都必须是 JSON 对象", "invalid_input")
            prepared_items.append(_prepare_record(item, table_schema))

        db_path = DB_DIR / f"{db_name}.db"
        saved = 0
        skipped = 0
        errors = 0
        error_messages = []
        with _db_pool.connection(db_path) as conn:
            cursor = conn.cursor()
            conn.execute("BEGIN TRANSACTION")
            try:
                for index, record in enumerate(prepared_items):
                    try:
                        _ensure_table(cursor, table, record, table_schema)
                        _ensure_schema_indexes(cursor, table, table_schema)
                        inserted, _sole_id = _insert_record(cursor, table, record, table_schema)
                        if not inserted:
                            skipped += 1
                            continue
                        saved += 1
                    except Exception as e:
                        errors += 1
                        message = f"第 {index + 1} 条失败: {e}"
                        error_messages.append(message)
                        logger.warning(f"批量插入单条失败: {e}")
                        if atomic:
                            conn.rollback()
                            return _error_result(message, "batch_save_failed")

                conn.commit()
                return json.dumps({
                    "saved": saved,
                    "skipped": skipped,
                    "errors": errors,
                    "total": len(items),
                    "atomic": atomic,
                    "rolled_back": False,
                    "error_messages": error_messages[:10],
                }, ensure_ascii=False, indent=2)
            except Exception as e:
                conn.rollback()
                return _error_result(str(e), "batch_save_failed")
    except Exception as e:
        return _error_result(str(e), "batch_save_failed")

@mcp.tool()
def query_db(db_name: str = "crawler_data", table: str = "products",
             limit: int = 20, offset: int = 0, where: str = "") -> str:
    """
    查询 SQLite 数据库中的数据。

    Args:
        db_name: 数据库名称
        table: 表名（默认 products）
        limit: 返回条数
        offset: 偏移量
        where: WHERE 条件（可选，如 "price > 100" 或 "title LIKE '%关键词%'"）

    Returns:
        查询结果 JSON，包含 total、count、data
    """
    try:
        _validate_identifier(db_name, "数据库名")
        _validate_identifier(table, "表名")

        db_path = DB_DIR / f"{db_name}.db"
        if not db_path.exists():
            return _error_result(f"数据库 {db_name} 不存在", "not_found")

        with _db_pool.connection(db_path, row_factory=sqlite3.Row) as conn:
            cursor = conn.cursor()
            table_sql = _quote_identifier(table)
            allowed_columns = _get_table_columns(cursor, table)
            where_clause, where_params = _build_where_clause(where, allowed_columns)

            cursor.execute(f'SELECT COUNT(*) FROM {table_sql} {where_clause}', where_params)
            total = cursor.fetchone()[0]

            cursor.execute(f'SELECT * FROM {table_sql} {where_clause} LIMIT ? OFFSET ?',
                           [*where_params, limit, offset])
            rows = [dict(row) for row in cursor.fetchall()]

        return json.dumps({"total": total, "count": len(rows), "data": rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "query_failed")

@mcp.tool()
def export_db(db_name: str = "crawler_data", table: str = "products",
              format: str = "csv", where: str = "") -> str:
    """
    导出数据库数据到文件。

    Args:
        db_name: 数据库名称
        table: 表名
        format: 导出格式 (csv/json)
        where: WHERE 条件（可选）

    Returns:
        导出文件路径
    """
    try:
        _validate_identifier(db_name, "数据库名")
        _validate_identifier(table, "表名")

        db_path = DB_DIR / f"{db_name}.db"
        if not db_path.exists():
            return _error_result(f"数据库 {db_name} 不存在", "not_found")

        with _db_pool.connection(db_path, row_factory=sqlite3.Row) as conn:
            cursor = conn.cursor()
            table_sql = _quote_identifier(table)
            allowed_columns = _get_table_columns(cursor, table)
            where_clause, where_params = _build_where_clause(where, allowed_columns)

            cursor.execute(f'SELECT * FROM {table_sql} {where_clause}', where_params)
            rows = [dict(row) for row in cursor.fetchall()]

        if not rows:
            return "没有数据可导出"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if format == "csv":
            filename = f"{db_name}_{table}_{timestamp}.csv"
            filepath = OUTPUT_DIR / filename
            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        else:
            filename = f"{db_name}_{table}_{timestamp}.json"
            filepath = OUTPUT_DIR / filename
            filepath.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

        return f"已导出 {len(rows)} 条数据到: {filepath}"
    except Exception as e:
        return _error_result(str(e), "export_failed")

@mcp.tool()
def list_databases() -> str:
    """
    列出所有数据库及其表信息。

    Returns:
        数据库列表 JSON
    """
    dbs = []
    for f in DB_DIR.glob("*.db"):
        try:
            with _db_pool.connection(f) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = []
                for (table_name,) in cursor.fetchall():
                    cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}")
                    count = cursor.fetchone()[0]
                    tables.append({"name": table_name, "records": count})
            dbs.append({"name": f.stem, "tables": tables, "path": str(f)})
        except Exception as e:
            logger.warning(f"读取数据库失败 {f}: {e}")
    return json.dumps(dbs, ensure_ascii=False, indent=2)

@mcp.tool()
def clear_cache(cache_type: str = "all", domain: str = "") -> str:
    """
    清除缓存。

    Args:
        cache_type: 缓存类型 (all/http/browser)
        domain: 按域名清除（如 "baidu.com"），为空则按类型清除

    Returns:
        清除结果
    """
    count = 0
    for f in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if domain:
                if domain in data.get("url", ""):
                    f.unlink()
                    count += 1
            elif cache_type == "all":
                f.unlink()
                count += 1
            else:
                req_type = data.get("type", 1)
                if cache_type == "http" and req_type in (1, 2):
                    f.unlink()
                    count += 1
                elif cache_type == "browser" and req_type == 3:
                    f.unlink()
                    count += 1
        except Exception:
            pass
    return f"已清除 {count} 个缓存文件"

@mcp.tool()
def frontier_add_urls(urls: str, priority: int = 0, kind: str = "page",
                      depth: int = 0, parent_url: str = "", payload: str = "{}",
                      allow_private: bool = False) -> str:
    """
    将 URL 加入持久化 Frontier 队列，支持去重、优先级、断点续爬。

    Args:
        urls: JSON 数组或按行分隔的 URL
        priority: 优先级，越大越先抓
        kind: URL 类型，如 list/detail/page
        depth: 抓取深度
        parent_url: 来源 URL
        payload: 附加 JSON 对象
        allow_private: 是否允许 localhost/内网 URL 入队
    """
    try:
        raw_items = _json_or_lines(urls)
        valid_urls = []
        rejected = []
        for item in raw_items:
            url = item.get("url") if isinstance(item, dict) else str(item)
            try:
                _validate_url(url, allow_private=allow_private)
                valid_urls.append(url)
            except Exception as exc:
                rejected.append({"url": url, "error": str(exc)})
        result = _frontier.add_urls(
            valid_urls,
            priority=priority,
            kind=kind,
            depth=depth,
            parent_url=parent_url,
            payload=_json_obj(payload, {}),
        )
        result["rejected"] = len(rejected)
        result["rejected_examples"] = rejected[:5]
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "frontier_add_failed")

@mcp.tool()
def frontier_next_batch(limit: int = 10, domain: str = "", worker_id: str = "mcp",
                        lease_seconds: int = 900) -> str:
    """
    从 Frontier 领取下一批 URL。领取后会进入 running 状态，超出 lease 会自动可重领。
    """
    try:
        rows = _frontier.next_batch(
            limit=limit,
            domain=domain,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        return _success_result({"count": len(rows), "items": rows})
    except Exception as e:
        return _error_result(str(e), "frontier_next_failed")

@mcp.tool()
def frontier_mark_done(items: str) -> str:
    """
    将 Frontier URL 标记为完成。items 支持 JSON 数组、单个 id/url 或按行分隔。
    """
    try:
        parsed = _json_or_lines(items)
        updated = _frontier.mark_done(parsed)
        return _success_result({"updated": updated})
    except Exception as e:
        return _error_result(str(e), "frontier_mark_done_failed")

@mcp.tool()
def frontier_mark_failed(items: str, error: str = "", retry: bool = True) -> str:
    """
    将 Frontier URL 标记为失败。retry=True 会重新放回 queued，False 会进入 failed。
    """
    try:
        parsed = _json_or_lines(items)
        updated = _frontier.mark_failed(parsed, error=error, retry=retry)
        return _success_result({"updated": updated, "retry": retry})
    except Exception as e:
        return _error_result(str(e), "frontier_mark_failed_failed")

@mcp.tool()
def frontier_stats() -> str:
    """查看 Frontier 队列统计、域名分布和 Bloom filter 配置。"""
    try:
        return _success_result(_frontier.stats())
    except Exception as e:
        return _error_result(str(e), "frontier_stats_failed")

@mcp.tool()
def save_cookie_profile(profile: str, cookies: str) -> str:
    """
    保存 Cookie Profile。profile 建议用域名，如 example.com；cookies 为 JSON 对象。
    """
    try:
        path = _cookie_store.save(profile, _json_obj(cookies, {}))
        return _success_result({"saved": True, "profile": profile, "path": str(path)})
    except Exception as e:
        return _error_result(str(e), "cookie_save_failed")

@mcp.tool()
def get_cookie_profile(profile: str, reveal: bool = False) -> str:
    """
    查看 Cookie Profile。默认只显示 cookie 名称；reveal=True 才返回完整值。
    """
    try:
        cookies = _cookie_store.load(profile)
        data = cookies if reveal else {key: "***" for key in cookies}
        return _success_result({"profile": profile, "cookies": data, "cookies_count": len(cookies)})
    except Exception as e:
        return _error_result(str(e), "cookie_get_failed")

@mcp.tool()
def list_cookie_profiles() -> str:
    """列出已保存的 Cookie Profile。"""
    try:
        return _success_result({"profiles": _cookie_store.list_profiles()})
    except Exception as e:
        return _error_result(str(e), "cookie_list_failed")

@mcp.tool()
def clear_cookie_profile(profile: str = "") -> str:
    """
    清除 Cookie Profile。profile 为空时清空全部本地 Cookie 文件。
    """
    try:
        removed = _cookie_store.clear(profile)
        return _success_result({"removed": removed})
    except Exception as e:
        return _error_result(str(e), "cookie_clear_failed")

@mcp.tool()
def draft_crawl_pipeline(goal: str, start_url: str = "", link_selector: str = "",
                         fields: str = "{}", output_format: str = "json",
                         output_name: str = "", db_name: str = "crawler_data",
                         table: str = "items", mode: str = "auto",
                         max_items: int = 100, use_frontier: bool = False,
                         allow_private: bool = False) -> str:
    """
    根据自然语言目标和少量参数生成可运行 Pipeline JSON，适合保存为模板后反复使用。
    """
    try:
        if start_url:
            _validate_url(start_url, allow_private=allow_private)
        field_defs = _json_obj(fields, {}) if fields else {}
        if not field_defs:
            field_defs = {"title": "h1"}
        output_format = output_format.lower().strip()
        if output_format not in {"json", "db", "both"}:
            return _error_result("output_format 只支持 json/db/both", "invalid_pipeline")
        pipeline = {
            "name": re.sub(r"[^a-zA-Z0-9_-]+", "_", goal.strip().lower())[:80] or "crawl_pipeline",
            "goal": goal,
            "mode": mode,
            "use_cache": True,
            "steps": [],
        }
        if start_url and link_selector:
            pipeline["steps"].append({
                "step": "crawl_list",
                "url": start_url,
                "selector": link_selector,
                "max_items": max_items,
            })
            if use_frontier:
                pipeline["steps"].extend([
                    {"step": "frontier_add", "source": "links", "priority": 10, "kind": "detail"},
                    {"step": "frontier_next", "limit": max_items, "worker_id": "pipeline"},
                ])
        elif start_url:
            pipeline["steps"].append({
                "step": "frontier_add",
                "urls": [start_url],
                "priority": 10,
                "kind": "detail",
            })
            pipeline["steps"].append({"step": "frontier_next", "limit": 1, "worker_id": "pipeline"})
        pipeline["steps"].append({
            "step": "crawl_products",
            "fields": field_defs,
            "max_items": max_items,
        })
        if output_format in {"db", "both"}:
            pipeline["steps"].append({"step": "save", "db": db_name, "table": table})
        if output_format in {"json", "both"}:
            pipeline["steps"].append({
                "step": "save_json",
                "filename": output_name or f"pipeline_{int(time.time())}.json",
            })
        return _success_result({"pipeline": pipeline})
    except Exception as e:
        return _error_result(str(e), "pipeline_draft_failed")

@mcp.tool()
def run_crawl_pipeline(pipeline_json: str, variables: str = "{}",
                       allow_private: bool = False) -> str:
    """
    运行 Pipeline DSL。支持 crawl_list、frontier_add、frontier_next、crawl_products、filter、save、save_json。
    """
    try:
        pipeline = _parse_pipeline(pipeline_json)
        rendered = render_template(pipeline, _json_obj(variables, {}))
        return _success_result(_run_pipeline_spec(rendered, allow_private=allow_private))
    except Exception as e:
        return _error_result(str(e), "pipeline_run_failed")

@mcp.tool()
def save_crawl_template(name: str, pipeline_json: str, description: str = "") -> str:
    """保存 Pipeline 模板，后续可用 run_crawl_template 传 variables 复用。"""
    try:
        pipeline = _parse_pipeline(pipeline_json)
        path = _template_store.save(name, pipeline, description)
        return _success_result({"saved": True, "name": name, "path": str(path)})
    except Exception as e:
        return _error_result(str(e), "template_save_failed")

@mcp.tool()
def list_crawl_templates() -> str:
    """列出已保存的采集 Pipeline 模板。"""
    try:
        return _success_result({"templates": _template_store.list()})
    except Exception as e:
        return _error_result(str(e), "template_list_failed")

@mcp.tool()
def get_crawl_template(name: str) -> str:
    """读取指定采集模板。"""
    try:
        return _success_result(_template_store.load(name))
    except Exception as e:
        return _error_result(str(e), "template_get_failed")

@mcp.tool()
def run_crawl_template(name: str, variables: str = "{}",
                       allow_private: bool = False) -> str:
    """按模板名运行采集 Pipeline，variables 支持替换模板中的 {{变量名}}。"""
    try:
        template = _template_store.load(name)
        pipeline = render_template(template.get("pipeline", {}), _json_obj(variables, {}))
        pipeline = _parse_pipeline(pipeline)
        return _success_result(_run_pipeline_spec(pipeline, allow_private=allow_private))
    except Exception as e:
        return _error_result(str(e), "template_run_failed")

@mcp.tool()
def set_proxy(proxy_url: str = "", proxy_type: str = "local") -> str:
    """
    设置代理。

    Args:
        proxy_url: 代理地址，如 http://127.0.0.1:7890 或 http://user:pass@host:port
        proxy_type: 代理类型 (local/pool)
                   local: 本地代理（Clash/V2Ray 等）
                   pool: 使用 proxy_pool.json 中的代理池

    Returns:
        设置结果
    """
    if proxy_type == "local" and proxy_url:
        _proxy_pool.set_local_proxy(proxy_url)
        return f"已设置本地代理: {proxy_url}"
    elif proxy_type == "pool":
        count = _proxy_pool.count
        return f"代理池中有 {count} 个代理" if count > 0 else "代理池为空，请编辑 proxy_pool.json"
    return "请提供代理地址"

@mcp.tool()
def start_crawl_job(url: str, job_type: str = "fetch", selector: str = "",
                    fields: str = "{}", mode: str = "auto", base_url: str = "",
                    max_items: int = 100, output_name: str = "",
                    save_to_db_flag: bool = False, db_name: str = "crawl_jobs",
                    table: str = "items", use_cache: bool = True,
                    respect_robots: bool = RESPECT_ROBOTS,
                    allow_private: bool = False, background: bool = True) -> str:
    """
    启动一个轻量采集任务。

    job_type:
    - fetch: 抓取页面并保存 HTML
    - crawl_list: 抓取列表页并用 selector 提取链接
    - crawl_product: 抓取详情页并用 fields 提取结构化数据
    """
    try:
        job_type = job_type.strip().lower()
        if job_type not in {"fetch", "crawl_list", "crawl_product"}:
            return _error_result("job_type 只支持 fetch/crawl_list/crawl_product", "invalid_job")
        if job_type == "crawl_list" and not selector:
            return _error_result("crawl_list 需要 selector", "invalid_job")
        if job_type == "crawl_product":
            json.loads(fields or "{}")

        job_id = _new_job_id()
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": "",
            "finished_at": "",
            "duration_ms": 0,
            "attempts": 1,
            "artifact": "",
            "db_result": "",
            "error": "",
            "result": {},
            "config": {
                "url": url,
                "job_type": job_type,
                "selector": selector,
                "fields": fields,
                "mode": mode,
                "base_url": base_url,
                "max_items": max(1, min(int(max_items), 1000)),
                "output_name": output_name,
                "save_to_db": save_to_db_flag,
                "db_name": db_name,
                "table": table,
                "use_cache": use_cache,
                "respect_robots": respect_robots,
                "allow_private": allow_private,
            },
        }
        _write_job(job)
        if background:
            _job_executor.submit(_run_crawl_job, job_id)
            return json.dumps({"job_id": job_id, "status": "queued"}, ensure_ascii=False, indent=2)

        _run_crawl_job(job_id)
        return json.dumps(_read_job(job_id), ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "job_start_failed")

@mcp.tool()
def get_job_status(job_id: str, include_result: bool = True) -> str:
    """查询采集任务状态。"""
    try:
        job = _read_job(job_id)
        if not include_result:
            job = dict(job)
            job.pop("result", None)
        return json.dumps(job, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "job_status_failed")

@mcp.tool()
def list_jobs(limit: int = 20, status: str = "") -> str:
    """列出最近采集任务。"""
    try:
        limit = max(1, min(int(limit), 200))
        status = status.strip().lower()
        jobs = []
        for path in sorted(JOB_DIR.glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if status and job.get("status") != status:
                continue
            summary = dict(job)
            summary.pop("result", None)
            jobs.append(summary)
            if len(jobs) >= limit:
                break
        return json.dumps({"count": len(jobs), "jobs": jobs}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "job_list_failed")

@mcp.tool()
def resume_job(job_id: str, background: bool = True) -> str:
    """按原配置重新运行一个采集任务。"""
    try:
        job = _read_job(job_id)
        if job.get("status") == "running":
            return _error_result("任务正在运行，不能重复启动", "job_running")
        job.update({
            "status": "queued",
            "started_at": "",
            "finished_at": "",
            "duration_ms": 0,
            "artifact": "",
            "db_result": "",
            "error": "",
            "result": {},
            "attempts": int(job.get("attempts", 0)) + 1,
        })
        _write_job(job)
        if background:
            _job_executor.submit(_run_crawl_job, job_id)
            return json.dumps({"job_id": job_id, "status": "queued", "attempts": job["attempts"]},
                              ensure_ascii=False, indent=2)
        _run_crawl_job(job_id)
        return json.dumps(_read_job(job_id), ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "job_resume_failed")


def _add_preflight_check(checks: list[dict], name: str, status: str, message: str, **extra) -> None:
    item = {"name": name, "status": status, "message": message}
    item.update({key: value for key, value in extra.items() if value not in (None, "")})
    checks.append(item)


def _check_writable_dir(checks: list[dict], name: str, path: Path) -> None:
    try:
        path.mkdir(exist_ok=True)
        marker = path / f".write_test_{uuid.uuid4().hex}.tmp"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        _add_preflight_check(checks, name, "ok", "目录存在且可写", path=str(path))
    except Exception as exc:
        _add_preflight_check(checks, name, "fail", f"目录不可写: {exc}", path=str(path))


def _check_json_client_config(checks: list[dict], name: str, path: Path, server_key: str = "crawler") -> None:
    if not path.exists():
        _add_preflight_check(
            checks,
            name,
            "warn",
            "客户端配置不存在，运行 setup_mcp_clients.py 可自动生成",
            path=str(path),
        )
        return
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(config, ensure_ascii=False)
        if server_key not in text:
            _add_preflight_check(checks, name, "warn", "配置存在，但未找到 crawler server", path=str(path))
            return
        _add_preflight_check(checks, name, "ok", "配置存在且包含 crawler server", path=str(path))
    except Exception as exc:
        _add_preflight_check(checks, name, "fail", f"配置 JSON 无法解析: {exc}", path=str(path))


def _check_codex_config(checks: list[dict], path: Path) -> None:
    if not path.exists():
        _add_preflight_check(
            checks,
            "client_config_codex",
            "warn",
            "Codex 配置不存在，运行 setup_mcp_clients.py 可自动生成",
            path=str(path),
        )
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if "mcp_servers.crawler" in text and "unified_crawler_server.py" in text:
        _add_preflight_check(checks, "client_config_codex", "ok", "Codex 配置存在且指向 crawler", path=str(path))
    else:
        _add_preflight_check(checks, "client_config_codex", "warn", "Codex 配置存在，但未找到 crawler server", path=str(path))


def _project_version_from_file(path: Path, pattern: str) -> str:
    if not path.exists():
        return ""
    match = re.search(pattern, path.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else ""


# ============ v4.0 新工具 ============

@mcp.tool()
def fetch_pages_batch(urls: str, concurrency: int = 0,
                      headers: str = "{}", per_url_timeout: float = 0,
                      respect_robots: bool = RESPECT_ROBOTS,
                      allow_private: bool = False,
                      max_length_per_url: int = 0) -> str:
    """v4.0: 异步并发批量抓取（基于 httpx HTTP/2，未装 httpx 时退化为线程池）。

    比逐条 fetch_page 快 3-10 倍，适合列表页详情批抓、sitemap 批爬。

    Args:
        urls: JSON 数组或按行分隔的 URL，最多 200 条
        concurrency: 并发数，默认 5；建议 5-20，太大会被限速
        headers: 公共请求头 JSON
        per_url_timeout: 单条 URL 超时（秒），0 表示用全局默认
        respect_robots: 是否检查 robots.txt
        allow_private: 是否允许内网/本机
        max_length_per_url: 单条 HTML 截断长度，0 表示用全局 FETCH_MAX_LENGTH

    Returns:
        JSON 数组，每项 {url, ok, status, html?/error?, elapsed_ms}
    """
    try:
        url_list = _json_or_lines(urls)
        urls_clean: list[str] = []
        for item in url_list:
            url = item.get("url") if isinstance(item, dict) else str(item)
            urls_clean.append(url)
        if not urls_clean:
            return _error_result("urls 为空", "invalid_input")
        if len(urls_clean) > 200:
            return _error_result("一次最多 200 条 URL", "invalid_input")

        concurrency = concurrency or ASYNC_BATCH_DEFAULT_CONCURRENCY
        concurrency = max(1, min(int(concurrency), 50))
        max_len = int(max_length_per_url) or FETCH_MAX_LENGTH
        per_url_to = float(per_url_timeout) or float(REQUEST_TIMEOUT)
        parsed_headers = _json_obj(headers, {})

        def policy_check(target_url: str) -> None:
            _validate_url(target_url, allow_private=allow_private)
            _apply_request_policy(target_url, respect_robots=respect_robots,
                                  allow_private=allow_private)

        results = _async_backend.fetch_batch(
            urls_clean,
            concurrency=concurrency,
            headers=parsed_headers if parsed_headers else None,
            per_url_timeout=per_url_to,
            policy_check=policy_check,
        )
        # 截断长度 + 隐藏过长 html
        for item in results:
            if item.get("html"):
                item["html"] = item["html"][:max_len]
        return json.dumps({
            "count": len(results),
            "ok_count": sum(1 for r in results if r.get("ok")),
            "concurrency": concurrency,
            "results": results,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return _error_result(str(exc), "batch_fetch_failed",
                             "检查 URL 列表格式与网络连通性")


@mcp.tool()
def parse_html_advanced(html: str, selector: str,
                        selector_type: str = "css", attr: str = "") -> str:
    """v4.0: 多种选择器解析（CSS / XPath / JSONPath）。

    Args:
        html: HTML 内容（jsonpath 时是 JSON 字符串/对象）
        selector: 选择器表达式
        selector_type: css | xpath | jsonpath，默认 css
        attr: 仅 css 模式下生效，提取属性而非文本（如 "src"、"href"）

    Returns:
        提取结果 JSON 数组
    """
    try:
        results = _parsing_mod.parse_with_type(html, selector, selector_type, attr=attr)
        return json.dumps({
            "selector_type": selector_type,
            "selector": selector,
            "count": len(results) if isinstance(results, list) else 1,
            "results": results,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return _error_result(str(exc), "parse_advanced_failed",
                             "selector_type 仅支持 css/xpath/jsonpath；XPath 需安装 parsel")


@mcp.tool()
def normalize_job_records(records: str = "", input_path: str = "",
                          input_format: str = "auto") -> str:
    """
    将招聘岗位记录标准化为分析可用 Schema，并输出质量分级。

    支持输入：
    - records: CSV 文本、JSON 对象、JSON 数组或 {"records": [...]}。
    - input_path: 本地 CSV/JSON 文件路径。

    常见原始字段：
    title, location, salary_or_benefits, source_channel,
    description_requirements, url, fetch_status, publish_date。
    """
    try:
        rows = _load_job_records(records=records, input_path=input_path, input_format=input_format)
        result = _normalize_job_records(rows)
        return _success_result(_v5_envelope(
            True,
            data=result,
            diagnostics={
                "input_count": len(rows),
                "schema": [
                    "title_raw", "title_normalized", "job_category",
                    "country", "province_state", "city", "is_remote",
                    "currency", "salary_min", "salary_max", "salary_period",
                    "salary_negotiable", "benefits", "description_clean",
                    "source_channel", "url", "fetch_status", "publish_date",
                    "fetch_time", "quality_grade",
                ],
            },
            recommendations=[
                {
                    "action": "filter_by_quality_grade",
                    "why": "A/B rows are safer for analysis; C/D rows should be reviewed before decision use",
                }
            ],
            records=result["records"],
            summary=result["summary"],
        ))
    except Exception as exc:
        return _error_result(str(exc), "normalize_jobs_failed",
                             "检查 records/input_path/input_format，确保输入是 CSV 或 JSON")


@mcp.tool()
def frontier_rebuild_bloom() -> str:
    """v4.0: 从 SQLite 全量重建 Bloom 位图。bloom 文件损坏或参数变更后调用。"""
    try:
        rebuilt = _frontier.rebuild_bloom_from_db()
        return _success_result({"rebuilt_count": rebuilt, "bloom": _frontier.bloom.info()})
    except Exception as exc:
        return _error_result(str(exc), "bloom_rebuild_failed")


@mcp.tool()
def domain_memory_stats(limit: int = 50) -> str:
    """v4.0: 查看域名成功模式记忆（auto-mode 升级用）。"""
    try:
        if not _domain_memory:
            return _error_result("domain_memory 未启用", "feature_disabled",
                                 "设置 CRAWLER_DOMAIN_MEMORY_ENABLED=true 后重启")
        return _success_result({
            "stats": _domain_memory.stats(),
            "records": _domain_memory.all_records(limit=limit),
        })
    except Exception as exc:
        return _error_result(str(exc), "domain_memory_stats_failed")


@mcp.tool()
def domain_memory_reset(domain: str = "") -> str:
    """v4.0: 清空指定域名的成功模式记忆。domain 为空时清全部。"""
    try:
        if not _domain_memory:
            return _error_result("domain_memory 未启用", "feature_disabled")
        if domain:
            removed = 1 if _domain_memory.reset(domain) else 0
            return _success_result({"removed": removed, "domain": domain})
        # 全部清除：删整个 DB 重建
        _domain_memory.db_path.unlink(missing_ok=True)
        globals()["_domain_memory"] = DomainMemory(_domain_memory.db_path)
        return _success_result({"removed": "all"})
    except Exception as exc:
        return _error_result(str(exc), "domain_memory_reset_failed")


@mcp.tool()
def diagnose_crawler_setup() -> str:
    """
    运行部署前体检，检查依赖、目录、客户端配置、安全默认值和版本一致性。

    Returns:
        体检报告 JSON
    """
    try:
        checks: list[dict] = []

        pyproject_version = _project_version_from_file(PROJECT_ROOT / "pyproject.toml", r'version\s*=\s*"([^"]+)"')
        lock_version = _project_version_from_file(
            PROJECT_ROOT / "uv.lock",
            r'name\s*=\s*"mcp-crawler-cluster"\s+version\s*=\s*"([^"]+)"',
        )
        if pyproject_version == SERVER_VERSION == lock_version:
            _add_preflight_check(checks, "version_alignment", "ok", f"版本一致: {SERVER_VERSION}")
        else:
            _add_preflight_check(
                checks,
                "version_alignment",
                "fail",
                "版本不一致，请同步 pyproject.toml、uv.lock 和 SERVER_VERSION",
                server_version=SERVER_VERSION,
                pyproject_version=pyproject_version or "missing",
                lock_version=lock_version or "missing",
            )

        for name, path in [
            ("dir_data", DATA_DIR),
            ("dir_output", OUTPUT_DIR),
            ("dir_cache", CACHE_DIR),
            ("dir_databases", DB_DIR),
            ("dir_schemas", SCHEMA_DIR),
            ("dir_logs", LOG_DIR),
            ("dir_jobs", JOB_DIR),
            ("dir_frontier", FRONTIER_DIR),
            ("dir_templates", TEMPLATE_DIR),
            ("dir_cookies", COOKIE_DIR),
        ]:
            _check_writable_dir(checks, name, path)

        for name, available, label in [
            ("dependency_curl_cffi", HAS_CURL_CFFI, "curl_cffi TLS 指纹伪装"),
            ("dependency_playwright", HAS_PLAYWRIGHT, "Playwright 浏览器渲染"),
            ("dependency_fake_useragent", HAS_FAKE_UA, "fake_useragent UA 轮换"),
        ]:
            _add_preflight_check(
                checks,
                name,
                "ok" if available else "warn",
                f"{label}{'可用' if available else '不可用，相关能力会降级'}",
            )

        _check_codex_config(checks, PROJECT_ROOT / ".codex" / "config.toml")
        _check_json_client_config(checks, "client_config_claude_code", PROJECT_ROOT / ".mcp.json")
        _check_json_client_config(checks, "client_config_vscode", PROJECT_ROOT / ".vscode" / "mcp.json")

        proxy_file_status = "ok"
        proxy_file_message = "proxy_pool.json 可解析"
        try:
            json.loads(PROXY_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            proxy_file_status = "fail"
            proxy_file_message = f"proxy_pool.json 无法解析: {exc}"
        _add_preflight_check(checks, "proxy_pool_config", proxy_file_status, proxy_file_message, path=str(PROXY_FILE))

        _add_preflight_check(
            checks,
            "security_tls_verify",
            "ok" if VERIFY_TLS else "fail",
            "TLS 默认校验已开启" if VERIFY_TLS else "TLS 默认校验已关闭，企业和公网采集不建议这样运行",
        )
        _add_preflight_check(
            checks,
            "security_robots",
            "ok" if RESPECT_ROBOTS else "warn",
            "robots.txt 默认遵守" if RESPECT_ROBOTS else "robots.txt 默认未遵守，上线前建议开启",
        )
        _add_preflight_check(
            checks,
            "security_private_network_guard",
            "ok" if not ALLOW_PRIVATE_NETS else "warn",
            "默认阻止内网/本机目标" if not ALLOW_PRIVATE_NETS else "默认允许内网/本机目标，建议仅在可信内网环境使用",
        )
        _add_preflight_check(
            checks,
            "security_request_private_override",
            "ok" if not ALLOW_REQUEST_PRIVATE_OVERRIDE else "warn",
            "请求级 allow_private=True 覆盖已禁用" if not ALLOW_REQUEST_PRIVATE_OVERRIDE else "请求可显式启用 allow_private=True，建议仅在个人可信环境使用",
        )
        _add_preflight_check(
            checks,
            "security_insecure_tls_override",
            "ok" if not ALLOW_INSECURE_TLS_OVERRIDE else "warn",
            "请求级 verify_tls=False 覆盖已禁用" if not ALLOW_INSECURE_TLS_OVERRIDE else "请求可显式关闭 TLS 校验，建议仅在个人可信环境使用",
        )
        _add_preflight_check(
            checks,
            "security_browser_sandbox",
            "ok" if not BROWSER_ALLOW_UNSAFE_FLAGS else "fail",
            "浏览器未默认启用 unsafe flags" if not BROWSER_ALLOW_UNSAFE_FLAGS else "浏览器默认启用了 unsafe flags，需要明确隔离边界",
        )
        _add_preflight_check(
            checks,
            "domain_policy",
            "ok" if ALLOWED_DOMAINS or BLOCKED_DOMAINS else "warn",
            "已配置域名 allow/block 策略" if ALLOWED_DOMAINS or BLOCKED_DOMAINS else "个人使用可为空，上线前建议配置 CRAWLER_ALLOWED_DOMAINS 或 CRAWLER_BLOCKED_DOMAINS",
            allowed_domains=sorted(ALLOWED_DOMAINS),
            blocked_domains=sorted(BLOCKED_DOMAINS),
        )
        frontier_counts = _frontier.stats().get("status_counts", {})
        _add_preflight_check(
            checks,
            "frontier_ready",
            "ok",
            "URL Frontier 可用，支持去重、优先级、租约领取和断点续爬",
            status_counts=frontier_counts,
        )
        _add_preflight_check(
            checks,
            "pipeline_templates_ready",
            "ok",
            "Pipeline DSL 与模板目录可用，可用自然语言先生成模板再运行",
            templates=len(_template_store.list()),
        )
        _add_preflight_check(
            checks,
            "cookie_persistence",
            "ok" if PERSIST_COOKIES else "warn",
            "Cookie/Session 持久化已开启" if PERSIST_COOKIES else "Cookie/Session 持久化已关闭",
            profiles=len(_cookie_store.list_profiles()),
        )

        counts = {
            "ok": sum(1 for item in checks if item["status"] == "ok"),
            "warn": sum(1 for item in checks if item["status"] == "warn"),
            "fail": sum(1 for item in checks if item["status"] == "fail"),
        }
        summary_status = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
        report = {
            "version": SERVER_VERSION,
            "summary": {
                "status": summary_status,
                **counts,
                "personal_use_ready": counts["fail"] == 0,
                "enterprise_ready": counts["fail"] == 0 and counts["warn"] == 0,
            },
            "checks": checks,
            "next_actions": [
                item["message"] for item in checks
                if item["status"] in {"warn", "fail"}
            ][:8],
        }
        return json.dumps(report, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "diagnose_failed")


@mcp.tool()
def get_recent_events(limit: int = 50, event_type: str = "", domain: str = "") -> str:
    """
    查看最近的结构化运行事件。用于排查最近抓取成功/失败、耗时、缓存命中等情况。

    Args:
        limit: 返回条数，最大 500
        event_type: 事件类型筛选，如 fetch
        domain: 域名包含筛选
    """
    try:
        events = _read_recent_events(limit=limit, event_type=event_type, domain=domain)
        return json.dumps({"count": len(events), "events": events}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "events_failed")

@mcp.tool()
def get_metrics(limit: int = 500) -> str:
    """
    汇总最近运行事件的轻量指标，帮助判断失败率、慢域名、缓存命中与模式分布。
    """
    try:
        events = _read_recent_events(limit=limit)
        fetch_events = [e for e in events if e.get("event") == "fetch"]
        by_domain: dict[str, dict] = {}
        modes: dict[str, int] = {}
        failures: dict[str, int] = {}
        cache_hits = 0
        total_duration = 0

        for event in fetch_events:
            domain = event.get("domain") or "unknown"
            item = by_domain.setdefault(domain, {
                "count": 0,
                "success": 0,
                "fail": 0,
                "total_duration_ms": 0,
                "avg_duration_ms": 0,
            })
            duration = int(event.get("duration_ms") or 0)
            item["count"] += 1
            item["total_duration_ms"] += duration
            total_duration += duration
            if event.get("success"):
                item["success"] += 1
            else:
                item["fail"] += 1
                err = event.get("error_type") or "unknown"
                failures[err] = failures.get(err, 0) + 1
            mode = event.get("mode") or "unknown"
            modes[mode] = modes.get(mode, 0) + 1
            if event.get("cache_hit"):
                cache_hits += 1

        for item in by_domain.values():
            if item["count"]:
                item["avg_duration_ms"] = round(item["total_duration_ms"] / item["count"])
            item.pop("total_duration_ms", None)

        result = {
            "event_count": len(events),
            "fetch_count": len(fetch_events),
            "success": sum(1 for e in fetch_events if e.get("success")),
            "fail": sum(1 for e in fetch_events if not e.get("success")),
            "cache_hits": cache_hits,
            "avg_duration_ms": round(total_duration / len(fetch_events)) if fetch_events else 0,
            "modes": modes,
            "failures": failures,
            "domains": by_domain,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _error_result(str(e), "metrics_failed")

@mcp.tool()
def draft_site_spec(goal: str, start_url: str, list_selector: str,
                    fields: str, site: str = "", mode: str = "auto",
                    pagination: str = "{}", variants: str = "{}",
                    wait_selector: str = "", render_time: float = 3.0,
                    scroll_count: int = 0, scroll_delay: float = 1.0) -> str:
    """
    起草 fnspider site_spec.json。适合先由 MCP 分析网页后，把选择器固化成主爬虫框架可运行配置。
    fields 示例: {"title":"h1","price":".price","image_src":"img@src"}
    """
    try:
        spec = _draft_site_spec(
            goal=goal,
            start_url=start_url,
            list_selector=list_selector,
            fields=fields,
            site=site,
            mode=mode,
            pagination=pagination,
            variants=variants,
            wait_selector=wait_selector,
            render_time=render_time,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
        )
        return _success_result({"spec": spec})
    except Exception as e:
        return _error_result(str(e), "site_spec_draft_failed")

@mcp.tool()
def validate_site_spec(spec: str, sample_detail_url: str = "",
                       mode: str = "auto", use_cache: bool = True,
                       respect_robots: bool = RESPECT_ROBOTS,
                       allow_private: bool = False) -> str:
    """
    抓取样本页面并验证 site_spec 的列表链接和详情字段命中率。
    sample_detail_url 为空时，会用列表页提取到的第一条链接作为详情样本。
    """
    try:
        spec_obj = json.loads(spec)
        shape_issues = _validate_spec_shape(spec_obj)
        if shape_issues:
            return _success_result({
                "ok": False,
                "issues": shape_issues,
                "summary": {},
                "samples": {},
            })
        first_start = spec_obj["start_urls"][0]
        start_url = first_start if isinstance(first_start, str) else first_start.get("url", "")
        list_html = _smart_fetch(
            start_url,
            mode=mode or spec_obj.get("mode", "auto"),
            use_cache=use_cache,
            respect_robots=respect_robots,
            allow_private=allow_private,
        )
        preliminary = _validate_spec_against_html(spec_obj, list_html)
        detail_url = sample_detail_url
        if not detail_url:
            detail_url = (preliminary.get("samples", {}).get("links") or [""])[0]
        detail_html = ""
        if detail_url:
            detail_html = _smart_fetch(
                detail_url,
                mode=mode or spec_obj.get("mode", "auto"),
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        result = _validate_spec_against_html(spec_obj, list_html, detail_html)
        result["sample_detail_url"] = detail_url
        result["recommendation"] = (
            "ready_to_export" if result.get("summary", {}).get("score", 0) >= 0.75
            else "tune_selectors_and_revalidate"
        )
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "site_spec_validate_failed")

@mcp.tool()
def export_site_spec_to_spider(spec: str, spider_root: str = "") -> str:
    """
    将 site_spec.json 和可运行 runner 脚本导出到 spider_Uvex。
    默认目录来自 SPIDER_UVEX_ROOT，当前默认为 F:\\datawork\\spider_Uvex。
    """
    try:
        spec_obj = json.loads(spec)
        root = spider_root or str(SPIDER_UVEX_ROOT)
        result = _write_spider_package(spec_obj, root)
        result["command"] = f"python {Path(result['runner_path']).name}"
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "site_spec_export_failed")

@mcp.tool()
def list_site_spec_versions(site: str, spider_root: str = "") -> str:
    """列出导出到 spider_Uvex 的 site_spec 历史版本。"""
    try:
        root = spider_root or str(SPIDER_UVEX_ROOT)
        return _success_result({"versions": _list_spec_versions(root, site)})
    except Exception as e:
        return _error_result(str(e), "site_spec_versions_failed")

@mcp.tool()
def rollback_site_spec_version(site: str, version: str = "", spider_root: str = "") -> str:
    """回滚 site_spec 到指定版本；version 为空时回滚到最新历史版本。"""
    try:
        root = spider_root or str(SPIDER_UVEX_ROOT)
        return _success_result(_rollback_spec_version(root, site, version))
    except Exception as e:
        return _error_result(str(e), "site_spec_rollback_failed")

@mcp.tool()
def diagnose_access_strategy(url: str, target_selector: str = "",
                             mode: str = "auto", use_cache: bool = True,
                             wait_selector: str = "", render_time: float = 5.0,
                             wait_until: str = "domcontentloaded",
                             scroll_count: int = 0, scroll_delay: float = 1.0,
                             respect_robots: bool = RESPECT_ROBOTS,
                             allow_private: bool = False) -> str:
    """
    诊断授权采集时的访问/渲染策略：JS 壳、结构化数据、接口线索、选择器未命中、challenge/captcha 信号。
    不会自动破解验证码或访问控制；遇到 challenge 时会给出人工登录态/API/授权访问建议。
    """
    try:
        if mode == "browser":
            html = fetch_page_browser(
                url,
                wait_selector=wait_selector,
                render_time=render_time,
                wait_until=wait_until,
                scroll_count=scroll_count,
                scroll_delay=scroll_delay,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        else:
            html = _smart_fetch(
                url,
                mode=mode,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        if isinstance(html, str) and html.startswith("{"):
            with contextlib.suppress(Exception):
                parsed = json.loads(html)
                if parsed.get("success") is False:
                    return _success_result({
                        "ok": False,
                        "fetch_error": parsed,
                        "recommendations": [{
                            "type": "fetch_policy_or_access",
                            "action": "Review robots/private-network settings, permissions, login state, or use an official API.",
                        }],
                    })
        result = _diagnose_html(html, url=url, target_selector=target_selector)
        result["mode_used"] = mode
        result["classification"] = _classify_access_result(html=html)
        result["api_hints"] = _scan_api_hints(html, base_url=url, max_items=40)
        result["truncated_likely"] = len(html or "") >= max(0, FETCH_MAX_LENGTH - 16)
        result["recommendations"] = _merge_recommendations(
            result.get("recommendations", []),
            _access_probe_recommendations(
                [{
                    "mode": mode,
                    "use_proxy": False,
                    "ok": not bool(result.get("signals", {}).get("challenge")),
                    "html_bytes": len(html or ""),
                    "text_chars": result.get("signals", {}).get("text_chars", 0),
                    "classification": result["classification"],
                }],
                {
                    "local_proxy": getattr(_proxy_pool, "_local_proxy", "") or "",
                    "pool_count": _proxy_pool.count,
                    "suggested_local_proxy": "http://127.0.0.1:8800",
                },
                result["api_hints"],
            ),
        )
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "access_diagnose_failed")

@mcp.tool()
def probe_access_strategy(url: str, target_selector: str = "",
                          modes: str = "requests,curl_cffi,browser",
                          use_proxy: bool = False,
                          include_browser: bool = True,
                          use_cache: bool = False,
                          wait_selector: str = "", render_time: float = 5.0,
                          wait_until: str = "domcontentloaded",
                          scroll_count: int = 0, scroll_delay: float = 1.0,
                          respect_robots: bool = RESPECT_ROBOTS,
                          allow_private: bool = False) -> str:
    """
    v5.1 访问策略探测：对比 requests/curl_cffi/browser/代理路径，分类失败原因并输出 Agent 可用策略。

    不破解验证码或访问控制；发现 challenge 时只给出授权 Cookie、公开 API、降频或人工复核建议。
    """
    try:
        mode_list = [item.strip() for item in modes.split(",") if item.strip()]
        result = _probe_access_modes(
            url=url,
            target_selector=target_selector,
            modes=mode_list,
            use_proxy=use_proxy,
            include_browser=include_browser,
            use_cache=use_cache,
            respect_robots=respect_robots,
            allow_private=allow_private,
            wait_selector=wait_selector,
            render_time=render_time,
            wait_until=wait_until,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
        )
        return _success_result(_v5_envelope(
            result.get("summary", {}).get("ok", False),
            data={
                "url": result.get("url"),
                "summary": result.get("summary"),
                "api_hints": result.get("api_hints"),
                "proxy": result.get("proxy"),
            },
            diagnostics={"probes": result.get("probes", [])},
            recommendations=result.get("recommendations", []),
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "access_probe_failed")

@mcp.tool()
def observe_browser_network(url: str, wait_selector: str = "",
                            render_time: float = 5.0,
                            wait_until: str = "domcontentloaded",
                            scroll_count: int = 0, scroll_delay: float = 1.0,
                            resource_types: str = "xhr,fetch,document",
                            max_entries: int = 200,
                            capture_json_sample: bool = False,
                            respect_robots: bool = RESPECT_ROBOTS,
                            rate_limit: float = 0.0,
                            allow_private: bool = False) -> str:
    """
    v5.2 浏览器网络观测：捕获渲染过程中的 XHR/fetch/document/API 候选，辅助 Agent 发现公开数据接口和翻页参数。

    只做观测与解释，不破解验证码、签名或访问控制。
    """
    try:
        _apply_request_policy(url, respect_robots=respect_robots, rate_limit=rate_limit or None,
                              allow_private=allow_private)
        types = {item.strip() for item in resource_types.split(",") if item.strip()}
        result = _engine.observe_network(
            url,
            wait_until=wait_until,
            render_time=render_time,
            wait_selector=wait_selector,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
            capture_resource_types=types,
            max_entries=max_entries,
            capture_json_sample=capture_json_sample,
        )
        return _success_result(_v5_envelope(
            True,
            data={
                "url": result.get("url"),
                "main_status": result.get("main_status"),
                "page": result.get("page"),
                "network": result.get("network"),
            },
            diagnostics={"network": result.get("network")},
            recommendations=result.get("recommendations", []),
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "network_observe_failed")

@mcp.tool()
def infer_pagination_strategy(url: str, mode: str = "auto",
                              use_cache: bool = True,
                              wait_selector: str = "",
                              render_time: float = 5.0,
                              wait_until: str = "domcontentloaded",
                              scroll_count: int = 0,
                              scroll_delay: float = 1.0,
                              observe_network_flag: bool = True,
                              max_pages: int = 3,
                              respect_robots: bool = RESPECT_ROBOTS,
                              allow_private: bool = False) -> str:
    """
    识别列表页翻页方式：DOM next 链接、query page 参数、网络请求中的分页参数，并给出可采样的下一页 URL。
    """
    try:
        html = _fetch_rendered_or_static(
            url,
            mode=mode,
            use_cache=use_cache,
            wait_selector=wait_selector,
            render_time=render_time,
            wait_until=wait_until,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
            respect_robots=respect_robots,
            allow_private=allow_private,
        )
        dom_candidates = _pagination_candidates_from_html(html, url, max_candidates=30)
        network_summary = {}
        network_candidates: list[dict[str, Any]] = []
        if observe_network_flag and mode == "browser":
            observed = _engine.observe_network(
                url,
                wait_until=wait_until,
                render_time=max(1.0, min(render_time, 5.0)),
                wait_selector=wait_selector,
                scroll_count=scroll_count,
                scroll_delay=scroll_delay,
                capture_resource_types={"xhr", "fetch", "document"},
                max_entries=120,
                capture_json_sample=False,
            )
            network_summary = observed.get("network", {})
            for item in network_summary.get("candidates", []):
                if _has_strong_pagination_params(item.get("pagination_params") or {}):
                    network_candidates.append({
                        "type": "network_request",
                        "url": item.get("url"),
                        "method": item.get("method"),
                        "resource_type": item.get("resource_type"),
                        "confidence": min(0.9, 0.5 + item.get("score", 0) / 200),
                        "pagination_params": item.get("pagination_params"),
                        "evidence": "browser network response",
                    })
        candidates = sorted(dom_candidates + network_candidates, key=lambda item: item.get("confidence", 0), reverse=True)
        recommended = candidates[0] if candidates else {}
        sample_urls = _sample_next_urls_from_strategy(url, recommended, max_pages=max_pages)
        result = {
            "url": url,
            "recommended": recommended,
            "candidates": candidates[:30],
            "sample_next_urls": sample_urls,
            "network_summary": network_summary,
            "recommendations": [
                {
                    "type": "sample_pages_next",
                    "urls": sample_urls,
                    "reason": "先低频采样下一页，验证列表 selector 和详情链接是否稳定。",
                }
            ] if sample_urls else [{
                "type": "manual_pagination_review",
                "reason": "未发现高置信翻页方式，建议用浏览器截图或网络观测复核。",
            }],
        }
        return _success_result(_v5_envelope(
            bool(recommended),
            data={
                "url": url,
                "recommended": recommended,
                "sample_next_urls": sample_urls,
                "candidates": candidates[:10],
            },
            diagnostics={"network_summary": network_summary, "candidate_count": len(candidates)},
            recommendations=result["recommendations"],
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "pagination_infer_failed")

@mcp.tool()
def analyze_detail_samples(url: str, list_selector: str = "",
                           target_fields: str = "title,price,image_src,body",
                           mode: str = "auto", use_cache: bool = True,
                           sample_size: int = 3,
                           wait_selector: str = "",
                           render_time: float = 5.0,
                           wait_until: str = "domcontentloaded",
                           scroll_count: int = 0,
                           scroll_delay: float = 1.0,
                           respect_robots: bool = RESPECT_ROBOTS,
                           allow_private: bool = False) -> str:
    """
    从列表页抽取详情链接，低频进入详情页样本，推断详情页字段 selector 和样本值。
    这是“列表发现 -> 详情分析”的深度侦察入口。
    """
    try:
        sample_size = max(1, min(int(sample_size), 10))
        fields = [item.strip() for item in target_fields.split(",") if item.strip()]
        list_html = _fetch_rendered_or_static(
            url,
            mode=mode,
            use_cache=use_cache,
            wait_selector=wait_selector,
            render_time=render_time,
            wait_until=wait_until,
            scroll_count=scroll_count,
            scroll_delay=scroll_delay,
            respect_robots=respect_robots,
            allow_private=allow_private,
        )
        detail_links, selector_used = _extract_detail_links_from_list(
            list_html,
            url,
            list_selector=list_selector,
            max_links=max(sample_size * 3, 20),
        )
        detail_htmls: list[tuple[str, str]] = []
        failures = []
        for link in detail_links[:sample_size]:
            detail_url = link["url"]
            try:
                detail_html = _fetch_rendered_or_static(
                    detail_url,
                    mode=mode,
                    use_cache=use_cache,
                    wait_selector="",
                    render_time=max(1.0, min(render_time, 4.0)),
                    wait_until=wait_until,
                    scroll_count=0,
                    scroll_delay=scroll_delay,
                    respect_robots=respect_robots,
                    allow_private=allow_private,
                )
                detail_htmls.append((detail_url, detail_html))
            except Exception as exc:
                failures.append({"url": detail_url, "error": str(exc)[:300], "error_type": type(exc).__name__})
        inferred_site = urlparse(url).netloc.replace(":", "_").replace(".", "_") or "sampled_site"
        inference = _infer_site_spec_from_samples(
            list_html=list_html,
            detail_htmls=detail_htmls,
            base_url=url,
            site=inferred_site,
            goal="detail sample analysis",
            mode=mode,
            target_fields=fields,
            max_candidates=8,
        )
        detail_spec = inference.get("spec", {}).get("detail", {})
        samples = []
        for detail_url, detail_html in detail_htmls:
            samples.append({
                "url": detail_url,
                "html_bytes": len(detail_html or ""),
                "text_chars": len(BeautifulSoup(detail_html or "", "html.parser").get_text(" ", strip=True)),
                "values": _extract_fields_with_selectors(detail_html, detail_spec),
                "classification": _classify_access_result(html=detail_html),
            })
        risk_flags = _detail_field_risk_flags(detail_spec, samples)
        result = {
            "url": url,
            "list_selector_used": selector_used,
            "detail_links_found": len(detail_links),
            "detail_link_sample": detail_links[:10],
            "sampled_detail_count": len(detail_htmls),
            "failures": failures,
            "risk_flags": risk_flags,
            "site_spec": inference.get("spec"),
            "confidence": inference.get("confidence"),
            "per_page": inference.get("per_page"),
            "samples": samples,
            "recommendations": [
                {
                    "type": "detail_selectors",
                    "selectors": detail_spec,
                    "reason": "基于详情页样本投票得到，适合交给采集框架进一步验证。",
                },
                {
                    "type": "pagination_then_detail_pipeline",
                    "reason": "推荐先用 infer_pagination_strategy 扩展列表页，再用这些详情 selector 抽字段。",
                },
            ] + ([{
                "type": "detail_field_review",
                "risk_flags": risk_flags,
                "reason": "部分详情字段可能命中变体、购买框或报价噪声，正式采集前需要复核候选 selector。",
            }] if risk_flags else []),
        }
        return _success_result(_v5_envelope(
            len(detail_htmls) > 0,
            data={
                "url": url,
                "list_selector_used": selector_used,
                "detail_links_found": len(detail_links),
                "sampled_detail_count": len(detail_htmls),
                "site_spec": inference.get("spec"),
                "confidence": inference.get("confidence"),
                "risk_flags": risk_flags,
                "samples": samples,
            },
            diagnostics={"failures": failures, "per_page": inference.get("per_page")},
            recommendations=result["recommendations"],
            **_v5_compat(result),
        ))
    except Exception as e:
        return _error_result(str(e), "detail_sample_analysis_failed")

@mcp.tool()
def infer_site_selectors(url: str, target_fields: str = "list_link,title,price,image_src,body",
                         mode: str = "auto", use_cache: bool = True,
                         wait_selector: str = "", render_time: float = 5.0,
                         wait_until: str = "domcontentloaded",
                         scroll_count: int = 0, scroll_delay: float = 1.0,
                         max_candidates: int = 8,
                         respect_robots: bool = RESPECT_ROBOTS,
                         allow_private: bool = False) -> str:
    """
    自动推断并排序列表链接和详情字段 CSS 选择器候选。
    返回 candidates 和 best_spec_fragment，可作为 draft_site_spec/export_site_spec_to_spider 的输入。
    """
    try:
        fields = [item.strip() for item in target_fields.split(",") if item.strip()]
        if mode == "browser":
            html = fetch_page_browser(
                url,
                wait_selector=wait_selector,
                render_time=render_time,
                wait_until=wait_until,
                scroll_count=scroll_count,
                scroll_delay=scroll_delay,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        else:
            html = _smart_fetch(
                url,
                mode=mode,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        result = _infer_selector_candidates(
            html,
            base_url=url,
            target_fields=fields,
            max_candidates=max(1, min(int(max_candidates), 20)),
        )
        result["mode_used"] = mode
        result["fields_requested"] = fields
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "selector_inference_failed")

@mcp.tool()
def infer_site_spec_from_samples(url: str, goal: str = "",
                                 site: str = "", target_fields: str = "title,price,image_src,body",
                                 sample_size: int = 3, mode: str = "auto",
                                 use_cache: bool = True,
                                 wait_selector: str = "", render_time: float = 5.0,
                                 wait_until: str = "domcontentloaded",
                                 scroll_count: int = 0, scroll_delay: float = 1.0,
                                 max_candidates: int = 8,
                                 respect_robots: bool = RESPECT_ROBOTS,
                                 allow_private: bool = False) -> str:
    """
    多页面采样推断 site_spec：先推断列表链接，再抽样详情页，对字段选择器投票并输出置信度。
    """
    try:
        fields = [item.strip() for item in target_fields.split(",") if item.strip()]
        if mode == "browser":
            list_html = fetch_page_browser(
                url,
                wait_selector=wait_selector,
                render_time=render_time,
                wait_until=wait_until,
                scroll_count=scroll_count,
                scroll_delay=scroll_delay,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        else:
            list_html = _smart_fetch(
                url,
                mode=mode,
                use_cache=use_cache,
                respect_robots=respect_robots,
                allow_private=allow_private,
            )
        list_inference = _infer_selector_candidates(
            list_html,
            base_url=url,
            target_fields=["list_link"],
            max_candidates=max(1, min(int(max_candidates), 20)),
        )
        link_candidates = list_inference["fields"].get("list_link", [])
        detail_urls = []
        for candidate in link_candidates:
            for item in candidate.get("sample", []):
                if item not in detail_urls:
                    detail_urls.append(item)
                if len(detail_urls) >= max(1, min(int(sample_size), 10)):
                    break
            if len(detail_urls) >= max(1, min(int(sample_size), 10)):
                break

        detail_htmls = []
        for detail_url in detail_urls:
            try:
                detail_html = _smart_fetch(
                    detail_url,
                    mode=mode,
                    use_cache=use_cache,
                    respect_robots=respect_robots,
                    allow_private=allow_private,
                )
                detail_htmls.append((detail_url, detail_html))
            except Exception as exc:
                logger.warning(f"sample detail fetch failed: {detail_url}: {exc}")

        inferred_site = site or urlparse(url).netloc.replace(":", "_").replace(".", "_")
        result = _infer_site_spec_from_samples(
            list_html=list_html,
            detail_htmls=detail_htmls,
            base_url=url,
            site=inferred_site or "sampled_site",
            goal=goal,
            mode=mode,
            target_fields=fields,
            max_candidates=max(1, min(int(max_candidates), 20)),
        )
        result["sample_urls"] = detail_urls
        result["fetched_sample_count"] = len(detail_htmls)
        if mode == "browser":
            result["spec"]["wait_selector"] = wait_selector
            result["spec"]["sleep_time"] = render_time
            result["spec"]["scroll_count"] = scroll_count
            result["spec"]["scroll_delay"] = scroll_delay
        return _success_result(result)
    except Exception as e:
        return _error_result(str(e), "sampled_site_spec_inference_failed")

@mcp.tool()
def infer_category_tree(url: str, max_depth: int = 3,
                        sitemap_index_url: str = "", category_sitemap_url: str = "",
                        product_sitemap_url: str = "", mode: str = "auto",
                        use_cache: bool = True, render_navigation: bool = False,
                        respect_robots: bool = RESPECT_ROBOTS,
                        allow_private: bool = False) -> str:
    """
    自动发现并解析商品目录树，最多保留 max_depth 级。
    优先级：显式 sitemap -> robots.txt 中的 sitemap index -> 首页导航。
    若提供/发现 product sitemap，会用于覆盖报告和空目录候选过滤。
    """
    try:
        _validate_url(url, allow_private=allow_private)
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}/"
        robots_text = ""
        sitemap_index_xml = ""
        category_xml = ""
        product_xml = ""
        nav_html = ""

        if not sitemap_index_url:
            robots_url = urljoin(base, "/robots.txt")
            with contextlib.suppress(Exception):
                robots_text = _smart_fetch(
                    robots_url,
                    mode="curl_cffi" if HAS_CURL_CFFI else "requests",
                    use_cache=use_cache,
                    respect_robots=False,
                    allow_private=allow_private,
                )
                match = re.search(r"(?im)^sitemap:\s*(\S+)", robots_text)
                if match:
                    sitemap_index_url = match.group(1).strip()

        if sitemap_index_url:
            sitemap_index_xml = _fetch_full_text(
                sitemap_index_url,
                mode="curl_cffi" if HAS_CURL_CFFI else mode,
                use_cache=use_cache,
                allow_private=allow_private,
            )
            picked = _pick_sitemap_urls(sitemap_index_xml, base)
            category_sitemap_url = category_sitemap_url or picked.get("category_sitemap_url", "")
            product_sitemap_url = product_sitemap_url or picked.get("product_sitemap_url", "")

        if category_sitemap_url:
            category_xml = _fetch_full_text(
                category_sitemap_url,
                mode="curl_cffi" if HAS_CURL_CFFI else mode,
                use_cache=use_cache,
                allow_private=allow_private,
            )
        if product_sitemap_url:
            product_xml = _fetch_full_text(
                product_sitemap_url,
                mode="curl_cffi" if HAS_CURL_CFFI else mode,
                use_cache=use_cache,
                allow_private=allow_private,
            )
        if render_navigation or not category_xml:
            if render_navigation:
                nav_html = fetch_page_browser(
                    url,
                    wait_until="domcontentloaded",
                    render_time=5,
                    scroll_count=1,
                    use_cache=use_cache,
                    respect_robots=respect_robots,
                    allow_private=allow_private,
                )
            else:
                nav_html = _smart_fetch(
                    url,
                    mode=mode,
                    use_cache=use_cache,
                    respect_robots=respect_robots,
                    allow_private=allow_private,
                )

        tree = _build_category_tree(
            base_url=base,
            sitemap_index_xml=sitemap_index_xml,
            category_sitemap_xml=category_xml,
            product_sitemap_xml=product_xml,
            nav_html=nav_html,
            max_depth=max_depth,
        )
        tree["discovered"] = {
            "robots_sitemap": sitemap_index_url,
            "category_sitemap": category_sitemap_url,
            "product_sitemap": product_sitemap_url,
            "robots_found": bool(robots_text),
        }
        return _success_result(tree)
    except Exception as e:
        return _error_result(str(e), "category_tree_inference_failed")

@mcp.tool()
def get_crawl_status() -> str:
    """
    获取爬虫状态信息（功能可用性、代理状态、缓存状态等）。

    Returns:
        状态信息 JSON
    """
    cache_count = len(list(CACHE_DIR.glob("*.json")))
    db_count = len(list(DB_DIR.glob("*.db")))
    tool_count = _registered_tool_count()
    warnings = []
    if tool_count is None:
        warnings.append("无法读取 MCP 注册工具数量；FastMCP 内部结构可能已变化，但不代表工具不可用")

    status = {
        "version": SERVER_VERSION,
        "features": {
            "curl_cffi_tls伪装": HAS_CURL_CFFI,
            "playwright浏览器渲染": HAS_PLAYWRIGHT,
            "fake_useragent轮换": HAS_FAKE_UA,
            "sitemap解析": HAS_XML,
            "代理池": _proxy_pool.count > 0,
            "url_frontier": True,
            "pipeline_dsl": True,
            "template_crawling": True,
            "cookie_persistence": PERSIST_COOKIES,
        },
        "tools_count": tool_count,
        "warnings": warnings,
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "data_dir": str(DATA_DIR),
            "output_dir": str(OUTPUT_DIR),
            "cache_dir": str(CACHE_DIR),
            "database_dir": str(DB_DIR),
            "schema_dir": str(SCHEMA_DIR),
            "log_dir": str(LOG_DIR),
            "job_dir": str(JOB_DIR),
            "frontier_dir": str(FRONTIER_DIR),
            "template_dir": str(TEMPLATE_DIR),
            "cookie_dir": str(COOKIE_DIR),
        },
        "anti_crawl": {
            "ua_fingerprint_matching": True,
            "session_reuse_per_domain": True,
            "exponential_backoff": True,
            "rate_limiting": True,
            "robots_txt_check": RESPECT_ROBOTS,
            "tls_verify_default": VERIFY_TLS,
            "browser_unsafe_flags_enabled": BROWSER_ALLOW_UNSAFE_FLAGS,
            "playwright_stealth": "Canvas/WebGL/AudioContext/CDC/Permission",
            "per_domain_context": True,
            "spider_compatible_tools": True,
            "schema_managed_storage": True,
            "ssrf_private_network_guard": not ALLOW_PRIVATE_NETS,
            "crawl_jobs": True,
            "url_frontier": True,
            "pipeline_templates": True,
            "setup_doctor": True,
        },
        "proxy": {
            "local_proxy": _proxy_pool._local_proxy or "未设置",
            "pool_count": _proxy_pool.count,
            "pool_status": _proxy_pool.get_status() if _proxy_pool.count > 0 else [],
        },
        "cache": {
            "count": cache_count,
            "ttl_seconds": CACHE_TTL,
            "max_size_mb": CACHE_MAX_SIZE_MB,
            "prune_every_writes": CACHE_PRUNE_EVERY_WRITES,
            "directory": str(CACHE_DIR),
        },
        "logs": {
            "event_log": str(EVENT_LOG_FILE),
            "event_log_size_bytes": EVENT_LOG_FILE.stat().st_size if EVENT_LOG_FILE.exists() else 0,
            "tail_read_limit": EVENT_LOG_TAIL_LINES,
        },
        "jobs": {
            "directory": str(JOB_DIR),
            "count": len(list(JOB_DIR.glob("job_*.json"))),
            "workers": _job_executor._max_workers,
        },
        "frontier": {
            **_frontier.stats(),
            "directory": str(FRONTIER_DIR),
        },
        "templates": {
            "directory": str(TEMPLATE_DIR),
            "count": len(_template_store.list()),
        },
        "cookies": {
            "directory": str(COOKIE_DIR),
            "persistence_enabled": PERSIST_COOKIES,
            "profiles_count": len(_cookie_store.list_profiles()),
        },
        "databases": {
            "count": db_count,
            "directory": str(DB_DIR),
            "schema_count": len(list(SCHEMA_DIR.glob("*.json"))),
            "schema_directory": str(SCHEMA_DIR),
        },
        "config": {
            "fetch_max_length": FETCH_MAX_LENGTH,
            "cache_max_size_mb": CACHE_MAX_SIZE_MB,
            "db_pool_size": DB_POOL_SIZE,
            "request_timeout": REQUEST_TIMEOUT,
            "request_retry": REQUEST_RETRY,
            "retry_base_delay": RETRY_BASE_DELAY,
            "default_rate_limit_rps": DEFAULT_RATE_LIMIT,
            "browser_render_time": BROWSER_RENDER_TIME,
            "browser_headless": BROWSER_HEADLESS,
            "detect_challenge_pages": DETECT_CHALLENGE_PAGES,
            "challenge_patterns_count": len(CHALLENGE_PATTERNS),
            "max_domain_sessions": MAX_DOMAIN_SESSIONS,
            "max_browser_contexts": MAX_BROWSER_CONTEXTS,
            "frontier_bloom_capacity": FRONTIER_BLOOM_CAPACITY,
            "frontier_bloom_error_rate": FRONTIER_BLOOM_ERROR_RATE,
            "persist_cookies": PERSIST_COOKIES,
            "allow_private_networks": ALLOW_PRIVATE_NETS,
            "allow_request_private_override": ALLOW_REQUEST_PRIVATE_OVERRIDE,
            "allow_insecure_tls_override": ALLOW_INSECURE_TLS_OVERRIDE,
            "allowed_domains": sorted(ALLOWED_DOMAINS),
            "blocked_domains": sorted(BLOCKED_DOMAINS),
        },
    }
    return json.dumps(status, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    mcp.run()
