"""v4.0 强化能力测试：异步并发批抓 + 域名记忆 + 三级 auto-mode + 多选择器。"""

import json
import time

import pytest

import unified_crawler_server as server
from crawler_core import challenge
from crawler_core import parsing
from crawler_core.domain_memory import DomainMemory


def _local_site():
    from tests.test_unified_server import _local_site as _impl
    return _impl()


# ---------- 异步批抓 ----------

def test_fetch_pages_batch_returns_html_for_each_url():
    with _local_site() as site:
        result = json.loads(server.fetch_pages_batch(
            json.dumps([f"{site}/p/1", f"{site}/p/2", f"{site}/p/3"]),
            concurrency=3,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["count"] == 3
    assert result["ok_count"] == 3
    for item in result["results"]:
        assert item["ok"] is True
        assert "Local Product" in (item.get("html") or "")


def test_fetch_pages_batch_blocks_private_when_disallowed():
    with _local_site() as site:
        result = json.loads(server.fetch_pages_batch(
            json.dumps([site]),
            concurrency=1,
            respect_robots=False,
            allow_private=False,
        ))

    assert result["count"] == 1
    item = result["results"][0]
    assert item["ok"] is False
    assert "policy" in (item.get("error") or "").lower() or "private" in (item.get("error") or "").lower() \
           or "默认禁止" in (item.get("error") or "")


def test_fetch_pages_batch_concurrency_limit():
    """并发数被夹到合理区间（不超过 50）。"""
    with _local_site() as site:
        result = json.loads(server.fetch_pages_batch(
            json.dumps([f"{site}/p/{i}" for i in range(5)]),
            concurrency=999,  # 应被夹到 50
            respect_robots=False,
            allow_private=True,
        ))
    assert result["concurrency"] == 50


# ---------- 域名记忆 ----------

def test_domain_memory_records_success_and_lookup(tmp_path):
    mem = DomainMemory(tmp_path / "domain_memory.db")
    assert mem.lookup("never-seen.example") is None

    mem.record_success("baidu.com", "curl_cffi", impersonate="chrome120")
    rec = mem.lookup("baidu.com")
    assert rec is not None
    assert rec["preferred_mode"] == "curl_cffi"
    assert rec["impersonate"] == "chrome120"
    assert rec["fresh"] is True
    assert rec["success_streak"] >= 1


def test_domain_memory_resets_after_consecutive_failures(tmp_path):
    mem = DomainMemory(tmp_path / "domain_memory.db")
    mem.record_success("foo.example", "curl_cffi")

    for _ in range(3):
        mem.record_failure("foo.example", "curl_cffi", challenge="cf-challenge")

    rec = mem.lookup("foo.example")
    # 连续 3 次失败后 lookup 返回 None（建议重新评估）
    assert rec is None


def test_domain_memory_stats_tool_returns_records(tmp_path, monkeypatch):
    """domain_memory_stats 工具能返回记录。"""
    fake_mem = DomainMemory(tmp_path / "dm.db")
    fake_mem.record_success("test1.example", "requests")
    fake_mem.record_success("test2.example", "browser")

    monkeypatch.setattr(server, "_domain_memory", fake_mem)

    result = json.loads(server.domain_memory_stats(limit=10))
    assert result.get("success", True) is not False
    assert result["stats"]["total_domains"] == 2


# ---------- challenge 探测 ----------

def test_challenge_detect_in_html_cf_challenge():
    html = "<html><div class='cf-challenge-running'>...</div></html>"
    assert challenge.detect_in_html(html) == "cf-challenge"


def test_challenge_detect_chinese_keyword():
    html = "<html>请完成安全验证</html>"
    assert challenge.detect_in_html(html) == "请完成安全验证"


def test_challenge_detect_in_response_combined_signals():
    headers = {"cf-mitigated": "challenge", "cf-ray": "abc"}
    cookies = {"__cf_bm": "value"}
    res = challenge.detect_in_response(503, headers, cookies, "<html>captcha</html>")
    assert res["confidence"] == "high"
    assert any("cf-mitigated" in r for r in res["reasons"])


def test_challenge_detect_returns_none_for_normal_page():
    res = challenge.detect_in_response(200, {"content-type": "text/html"}, {},
                                        "<html><h1>Welcome</h1></html>")
    assert res["challenge"] == ""
    assert res["confidence"] == "none"


def test_challenge_is_challenge_status_codes():
    assert challenge.is_challenge_status(403) is True
    assert challenge.is_challenge_status(503) is True
    assert challenge.is_challenge_status(200) is False


# ---------- 多选择器 ----------

def test_parse_html_advanced_css_with_attr():
    html = '<html><a href="https://x.test/1">A</a><a href="https://x.test/2">B</a></html>'
    result = json.loads(server.parse_html_advanced(html, "a", "css", attr="href"))
    assert result["count"] == 2
    assert "https://x.test/1" in result["results"]


def test_parse_html_advanced_jsonpath_simple():
    payload = json.dumps({"data": {"items": [{"title": "T1"}, {"title": "T2"}]}})
    result = json.loads(server.parse_html_advanced(payload, "data.items.0.title", "jsonpath"))
    assert result["count"] == 1


def test_parse_html_advanced_invalid_type_returns_error():
    result = json.loads(server.parse_html_advanced("<html>", "h1", "voodoo"))
    assert result.get("success") is False or result.get("error") is True


# ---------- 三级 auto-mode 升级（基础链路） ----------

def test_auto_mode_records_domain_memory_on_success(tmp_path, monkeypatch):
    fake_mem = DomainMemory(tmp_path / "dm.db")
    monkeypatch.setattr(server, "_domain_memory", fake_mem)

    with _local_site() as site:
        server.fetch_page(site, mode="auto", use_cache=False,
                          respect_robots=False, allow_private=True)

    rec = fake_mem.lookup("127.0.0.1")
    if rec is None:
        # 兼容 site host 含端口
        records = fake_mem.all_records()
        assert any("127.0.0.1" in r["domain"] for r in records)
    else:
        assert rec["last_success_at"] > 0


def test_smart_fetch_records_escalation_in_event_log(monkeypatch):
    """挑战页探测命中时 escalation 链应被记录到 event log。"""
    # 用 unit-level fake：直接通过 _challenge_mod.detect_in_html 注入 hit
    import crawler_core.challenge as _ch

    real_detect = _ch.detect_in_html
    counter = {"calls": 0}

    def fake_detect(html, patterns=None, sample_size=200_000):
        counter["calls"] += 1
        if counter["calls"] == 1:
            return "cf-challenge"
        return ""

    monkeypatch.setattr(server._challenge_mod, "detect_in_html", fake_detect)

    with _local_site() as site:
        try:
            server.fetch_page(site, mode="auto", use_cache=False,
                              respect_robots=False, allow_private=True)
        except Exception:
            pass  # 如果浏览器不可用，会失败；但 event log 应已写入升级记录

    events = json.loads(server.get_recent_events(limit=5, event_type="fetch"))
    found_escalation = False
    for ev in events.get("events", []):
        if ev.get("escalations"):
            found_escalation = True
            break
    assert found_escalation, "auto-mode 升级链路应被记录到 event log"
