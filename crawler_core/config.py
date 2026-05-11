"""Configuration loading for the crawler MCP server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


LoggerFn = Callable[[str], None]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int, warn: LoggerFn | None = None) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        if warn:
            warn(f"{name} config is invalid; using default {default}")
        return default


def env_float(name: str, default: float, warn: LoggerFn | None = None) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        if warn:
            warn(f"{name} config is invalid; using default {default}")
        return default


def env_csv(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def path_from_env(project_root: Path, name: str, default: str) -> Path:
    configured = os.getenv(name, default)
    path = Path(configured)
    return path if path.is_absolute() else project_root / path


def runtime_path_from_env(project_root: Path, data_dir: Path, name: str, default: str) -> Path:
    configured = os.getenv(name)
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else project_root / path
    return data_dir / default


@dataclass(frozen=True)
class CrawlerConfig:
    project_root: Path
    server_version: str
    data_dir: Path
    output_dir: Path
    cache_dir: Path
    db_dir: Path
    schema_dir: Path
    log_dir: Path
    job_dir: Path
    frontier_dir: Path
    template_dir: Path
    cookie_dir: Path
    proxy_file: Path
    spider_uvex_root: Path
    fetch_max_length: int
    request_timeout: int
    request_retry: int
    retry_base_delay: float
    retry_max_delay: float
    browser_timeout: int
    browser_render_time: float
    cache_ttl: int
    cache_max_size_mb: int
    cache_prune_every_writes: int
    event_log_tail_lines: int
    db_pool_size: int
    frontier_bloom_capacity: int
    frontier_bloom_error_rate: float
    default_rate_limit: float
    verify_tls: bool
    respect_robots: bool
    persist_cookies: bool
    browser_headless: bool
    browser_allow_unsafe_flags: bool
    detect_challenge_pages: bool
    pin_dns: bool
    auto_mode_escalation: bool
    domain_memory_enabled: bool
    target_memory_enabled: bool
    async_batch_default_concurrency: int
    max_domain_sessions: int
    max_browser_contexts: int
    allow_private_nets: bool
    allow_request_private_override: bool
    allow_insecure_tls_override: bool
    allowed_domains: set[str]
    blocked_domains: set[str]

    def ensure_directories(self) -> None:
        for path in [
            self.data_dir,
            self.output_dir,
            self.cache_dir,
            self.db_dir,
            self.schema_dir,
            self.log_dir,
            self.job_dir,
            self.frontier_dir,
            self.template_dir,
            self.cookie_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def load_config(project_root: Path, server_version: str, warn: LoggerFn | None = None) -> CrawlerConfig:
    project_root = project_root.resolve()
    data_dir = path_from_env(project_root, "CRAWLER_DATA_DIR", ".")

    return CrawlerConfig(
        project_root=project_root,
        server_version=server_version,
        data_dir=data_dir,
        output_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_OUTPUT_DIR", "output"),
        cache_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_CACHE_DIR", "cache"),
        db_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_DB_DIR", "databases"),
        schema_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_SCHEMA_DIR", "schemas"),
        log_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_LOG_DIR", "logs"),
        job_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_JOB_DIR", "jobs"),
        frontier_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_FRONTIER_DIR", "frontier"),
        template_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_TEMPLATE_DIR", "templates"),
        cookie_dir=runtime_path_from_env(project_root, data_dir, "CRAWLER_COOKIE_DIR", "cookies"),
        proxy_file=project_root / "proxy_pool.json",
        spider_uvex_root=path_from_env(project_root, "SPIDER_UVEX_ROOT", "spider_Uvex"),
        fetch_max_length=env_int("FETCH_MAX_LENGTH", 80000, warn),
        request_timeout=env_int("REQUEST_TIMEOUT", 30, warn),
        request_retry=env_int("REQUEST_RETRY", 3, warn),
        retry_base_delay=env_float("RETRY_BASE_DELAY", 1.0, warn),
        retry_max_delay=env_float("RETRY_MAX_DELAY", 30.0, warn),
        browser_timeout=env_int("BROWSER_TIMEOUT", 30000, warn),
        browser_render_time=env_float("BROWSER_RENDER_TIME", 3.0, warn),
        cache_ttl=env_int("CACHE_TTL", 3600, warn),
        cache_max_size_mb=env_int("CACHE_MAX_SIZE_MB", 512, warn),
        cache_prune_every_writes=max(1, env_int("CACHE_PRUNE_EVERY_WRITES", 20, warn)),
        event_log_tail_lines=env_int("CRAWLER_EVENT_LOG_TAIL_LINES", 5000, warn),
        db_pool_size=max(1, env_int("CRAWLER_DB_POOL_SIZE", 8, warn)),
        frontier_bloom_capacity=max(1000, env_int("CRAWLER_FRONTIER_BLOOM_CAPACITY", 1_000_000, warn)),
        frontier_bloom_error_rate=env_float("CRAWLER_FRONTIER_BLOOM_ERROR_RATE", 0.01, warn),
        default_rate_limit=env_float("CRAWLER_DEFAULT_RATE_LIMIT", 2.0, warn),
        verify_tls=env_bool("CRAWLER_VERIFY_TLS", True),
        respect_robots=env_bool("CRAWLER_RESPECT_ROBOTS", True),
        persist_cookies=env_bool("CRAWLER_PERSIST_COOKIES", True),
        browser_headless=env_bool("CRAWLER_BROWSER_HEADLESS", True),
        browser_allow_unsafe_flags=env_bool("CRAWLER_BROWSER_ALLOW_UNSAFE_FLAGS", False),
        detect_challenge_pages=env_bool("CRAWLER_DETECT_CHALLENGE_PAGES", True),
        pin_dns=env_bool("CRAWLER_PIN_DNS", True),
        auto_mode_escalation=env_bool("CRAWLER_AUTO_MODE_ESCALATION", True),
        domain_memory_enabled=env_bool("CRAWLER_DOMAIN_MEMORY_ENABLED", True),
        target_memory_enabled=env_bool("CRAWLER_TARGET_MEMORY_ENABLED", True),
        async_batch_default_concurrency=max(1, env_int("CRAWLER_BATCH_CONCURRENCY", 5, warn)),
        max_domain_sessions=max(1, env_int("CRAWLER_MAX_DOMAIN_SESSIONS", 64, warn)),
        max_browser_contexts=max(1, env_int("CRAWLER_MAX_BROWSER_CONTEXTS", 16, warn)),
        allow_private_nets=env_bool("CRAWLER_ALLOW_PRIVATE_NETS", False),
        allow_request_private_override=env_bool("CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE", True),
        allow_insecure_tls_override=env_bool("CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE", True),
        allowed_domains=env_csv("CRAWLER_ALLOWED_DOMAINS"),
        blocked_domains=env_csv("CRAWLER_BLOCKED_DOMAINS"),
    )
