import json
import time
import ipaddress

from crawler_core.cache import CacheStore
from crawler_core.config import env_bool, load_config
from crawler_core.cookies import CookieStore
from crawler_core.events import EventLog
from crawler_core.frontier import URLFrontier
from crawler_core.security import (
    domain_matches,
    effective_allow_private,
    effective_verify_tls,
    is_private_target,
    validate_url,
)
from crawler_core.templates import TemplateStore, render_template


class _Logger:
    def debug(self, *_args, **_kwargs):
        return None


def test_cache_store_reads_expires_and_prunes(tmp_path):
    store = CacheStore(
        tmp_path,
        ttl_seconds=3600,
        max_size_mb=1,
        prune_every_writes=1,
        logger=_Logger(),
    )
    store.write("https://example.test/a", "hello", req_type=1, variant="v1")
    assert store.read("https://example.test/a", req_type=1, variant="v1") == "hello"

    cache_file = tmp_path / f"{store.key('https://example.test/a', 1, 'v1')}.json"
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["timestamp"] = time.time() - 7200
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    assert store.read("https://example.test/a", req_type=1, variant="v1") is None
    assert not cache_file.exists()

    for index in range(3):
        path = tmp_path / f"large_{index}.json"
        path.write_text(json.dumps({"text": "x" * 700_000}), encoding="utf-8")
    store.prune_if_needed()
    assert len(list(tmp_path.glob("large_*.json"))) < 3


def test_event_log_appends_and_reads_tail_without_utf8_damage(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, tail_lines=2, logger=_Logger())
    log.append({"event": "fetch", "domain": "old.example", "text": "旧数据"})
    log.append({"event": "fetch", "domain": "tail.example", "text": "请完成安全验证"})
    log.append({"event": "fetch", "domain": "tail.example", "text": "正常中文尾部"})

    recent = log.read_recent(limit=10, event_type="fetch")
    assert len(recent) == 2
    assert recent[0]["text"] == "请完成安全验证"
    assert recent[1]["text"] == "正常中文尾部"

    lines = EventLog.tail_file_lines(path, 2, _Logger())
    assert "\ufffd" not in "".join(lines)


def test_url_frontier_dedupes_leases_and_marks_done(tmp_path):
    frontier = URLFrontier(
        tmp_path / "frontier.db",
        tmp_path / "frontier.bloom",
        logger=_Logger(),
        bloom_capacity=10_000,
    )

    added = frontier.add_urls([
        "https://example.test/a",
        "https://example.test/a",
        "ftp://example.test/bad",
    ], priority=10, kind="detail")
    assert added["added"] == 1
    assert added["skipped"] == 1
    assert added["invalid"] == 1

    batch = frontier.next_batch(limit=1, worker_id="test")
    assert len(batch) == 1
    assert batch[0]["url"] == "https://example.test/a"

    assert frontier.mark_done([batch[0]["id"]]) == 1
    stats = frontier.stats()
    assert stats["status_counts"]["done"] == 1
    assert stats["bloom"]["capacity"] == 10_000


def test_template_store_renders_and_lists_templates(tmp_path):
    store = TemplateStore(tmp_path)
    pipeline = {
        "steps": [
            {"step": "crawl_list", "url": "{{url}}", "selector": "a"},
        ]
    }
    path = store.save("list_template", pipeline, "list pages")

    assert path.exists()
    loaded = store.load("list_template")
    rendered = render_template(loaded["pipeline"], {"url": "https://example.test"})

    assert rendered["steps"][0]["url"] == "https://example.test"
    templates = store.list()
    assert templates[0]["name"] == "list_template"
    assert templates[0]["steps"] == 1


def test_cookie_store_saves_lists_and_clears_profiles(tmp_path):
    store = CookieStore(tmp_path)

    path = store.save("example.com", {"session": "abc"})
    assert path.exists()
    assert store.load("example.com") == {"session": "abc"}

    merged = store.merge("example.com", {"theme": "dark"})
    assert merged["session"] == "abc"
    assert merged["theme"] == "dark"
    assert store.list_profiles()[0]["cookies_count"] == 2

    assert store.clear("example.com") == 1
    assert store.load("example.com") == {}


def test_config_loads_data_dir_and_runtime_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAWLER_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CRAWLER_CACHE_DIR", "custom-cache")
    monkeypatch.setenv("CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE", "false")

    config = load_config(tmp_path, "test")

    assert config.data_dir == tmp_path / "runtime"
    assert config.cache_dir == tmp_path / "custom-cache"
    assert config.output_dir == tmp_path / "runtime" / "output"
    assert config.allow_request_private_override is False
    assert env_bool("CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE", True) is False


def test_config_ensure_directories_creates_runtime_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAWLER_DATA_DIR", str(tmp_path / "data"))
    config = load_config(tmp_path, "test")

    config.ensure_directories()

    assert config.data_dir.is_dir()
    assert config.output_dir.is_dir()
    assert config.cookie_dir.is_dir()


def test_security_domain_policy_and_private_target_guards():
    assert domain_matches("www.example.com", {"example.com"})
    assert not domain_matches("www.example.com", {"other.test"})
    assert is_private_target(ipaddress.ip_address("127.0.0.1"))

    try:
        validate_url("http://127.0.0.1", allow_private=False)
    except ValueError as exc:
        assert "Private/local/reserved" in str(exc)
    else:
        raise AssertionError("private target should be rejected by default")

    assert validate_url("http://127.0.0.1", allow_private=True) == "http://127.0.0.1"


def test_security_request_level_overrides_can_be_locked_down():
    try:
        effective_allow_private(True, request_override_enabled=False)
    except PermissionError as exc:
        assert "allow_private=True" in str(exc)
    else:
        raise AssertionError("allow_private override should be rejected")

    try:
        effective_verify_tls(False, insecure_override_enabled=False)
    except PermissionError as exc:
        assert "verify_tls=False" in str(exc)
    else:
        raise AssertionError("insecure TLS override should be rejected")
