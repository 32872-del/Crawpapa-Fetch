"""Core building blocks for the crawler MCP server."""

from crawler_core.cache import CacheStore
from crawler_core.cookies import CookieStore
from crawler_core.events import EventLog
from crawler_core.frontier import URLFrontier, PersistentBloomFilter
from crawler_core.templates import TemplateStore, render_template

# v4.0 新增能力模块
from crawler_core import challenge
from crawler_core import config
from crawler_core import dns_pin
from crawler_core import parsing
from crawler_core import security
from crawler_core.domain_memory import DomainMemory
from crawler_core.target_memory import TargetMemory
from crawler_core.async_http import AsyncBackend, HAS_HTTPX

__all__ = [
    "CacheStore",
    "CookieStore",
    "EventLog",
    "URLFrontier",
    "PersistentBloomFilter",
    "TemplateStore",
    "render_template",
    "challenge",
    "config",
    "dns_pin",
    "parsing",
    "security",
    "DomainMemory",
    "TargetMemory",
    "AsyncBackend",
    "HAS_HTTPX",
]
