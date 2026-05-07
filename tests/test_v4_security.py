"""v4.0 安全增强测试：DNS pin、LIKE 转义、Bloom 行为。"""

import json
import time

import unified_crawler_server as server
from crawler_core import dns_pin


def test_like_clause_escapes_wildcards_and_uses_escape_sql():
    db_name = f"test_like_{int(time.time() * 1000)}"
    server.save_to_db(
        json.dumps({"url": "https://example.test/foo", "title": "100% genuine"}),
        db_name=db_name,
        table="items",
    )
    server.save_to_db(
        json.dumps({"url": "https://example.test/bar", "title": "100 percent fake"}),
        db_name=db_name,
        table="items",
    )

    # 含字面量 % 的搜索 — v4.0 自动 escape，应该只命中第一条
    found = json.loads(server.query_db(
        db_name=db_name,
        table="items",
        where=json.dumps([{"field": "title", "op": "LIKE", "value": "100%"}]),
    ))
    titles = [row["title"] for row in found.get("data", [])]
    assert "100% genuine" in titles
    # 字面量 % 的 escape 让 "100 percent fake" 不应匹配 "100%"（因为没有字面量 %）
    assert "100 percent fake" not in titles


def test_like_clause_default_wildcard_wraps_when_no_explicit_wildcard():
    db_name = f"test_like_default_{int(time.time() * 1000)}"
    server.save_to_db(json.dumps({"url": "https://example.test/p", "title": "Apple Pie"}),
                      db_name=db_name, table="items")
    server.save_to_db(json.dumps({"url": "https://example.test/q", "title": "Banana Bread"}),
                      db_name=db_name, table="items")
    found = json.loads(server.query_db(
        db_name=db_name,
        table="items",
        where=json.dumps([{"field": "title", "op": "LIKE", "value": "Apple"}]),
    ))
    assert found["total"] == 1
    assert found["data"][0]["title"] == "Apple Pie"


def test_dns_pin_resolve_and_rewrite():
    """dns_pin 模块自身测试：解析 + url 重写正确。"""
    addresses = dns_pin.resolve_addresses("127.0.0.1")
    assert any(str(a) == "127.0.0.1" for a in addresses)

    pinned = dns_pin.build_pinned_address("https://example.com/path?x=1", addresses)
    assert pinned.hostname == "example.com"
    assert pinned.ip == "127.0.0.1"
    assert pinned.port == 443

    rewritten = dns_pin.rewrite_url_to_ip("https://example.com/path?x=1", pinned)
    assert "127.0.0.1" in rewritten
    assert "example.com" not in rewritten or rewritten.count("example.com") == 0
    assert rewritten.endswith("/path?x=1")


def test_dns_pin_rejects_unresolvable_host():
    import pytest
    with pytest.raises(ValueError):
        dns_pin.resolve_addresses("nonexistent-host-for-pin-test-12345.invalid")


def test_smart_fetch_dns_pinning_does_not_break_local_fetch():
    """打开 PIN_DNS（默认就开了）时本地 site fetch 仍然成功。"""
    from tests.test_unified_server import _local_site

    with _local_site() as site:
        result = server.fetch_page(
            site,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        )
        assert "Local Product" in result


def test_bloom_filter_dirty_flag_and_flush():
    from crawler_core.frontier import PersistentBloomFilter

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        bloom_path = Path(tmp) / "test.bloom"
        bloom = PersistentBloomFilter(bloom_path, capacity=1000, flush_every=100,
                                      flush_interval_seconds=60.0)
        assert bloom.info()["dirty"] is False

        bloom.add("https://example.com/a")
        info = bloom.info()
        assert info["dirty"] is True
        assert info["dirty_count"] == 1

        # maybe_flush 不应触发（未达阈值）
        flushed = bloom.maybe_flush()
        assert flushed is False
        assert not bloom_path.exists()

        # 强制 flush
        forced = bloom.flush()
        assert forced is True
        assert bloom_path.exists()
        assert bloom.info()["dirty"] is False


def test_frontier_lease_token_prevents_double_dispatch(tmp_path):
    """同一进程内两次 next_batch 不会取到同一 URL。"""
    from crawler_core.frontier import URLFrontier

    f = URLFrontier(
        tmp_path / "frontier.db",
        tmp_path / "frontier.bloom",
        logger=server.logger,
        bloom_capacity=10_000,
    )
    f.add_urls([f"https://example.test/p/{i}" for i in range(5)], priority=10)

    batch1 = f.next_batch(limit=3, worker_id="worker-1")
    batch2 = f.next_batch(limit=3, worker_id="worker-2")

    ids1 = {r["id"] for r in batch1}
    ids2 = {r["id"] for r in batch2}
    assert not (ids1 & ids2), f"租约 CAS 失效：worker-1 和 worker-2 取到了同一 URL"
    assert len(ids1) == 3
    assert len(ids2) == 2  # 总共只有 5 条


def test_frontier_rebuild_bloom_from_db(tmp_path):
    from crawler_core.frontier import URLFrontier

    f = URLFrontier(
        tmp_path / "frontier.db",
        tmp_path / "frontier.bloom",
        logger=server.logger,
        bloom_capacity=10_000,
    )
    f.add_urls(["https://example.test/a", "https://example.test/b"], priority=10)

    # 清空 bloom 后从 DB 重建
    f.bloom.reset()
    rebuilt = f.rebuild_bloom_from_db()
    assert rebuilt == 2

    # 重建后 might_contain 应该命中
    h_a = URLFrontier.url_hash("https://example.test/a")
    assert f.bloom.might_contain(h_a)


def test_frontier_returns_bloom_prefilter_hit_count(tmp_path):
    from crawler_core.frontier import URLFrontier

    f = URLFrontier(
        tmp_path / "frontier.db",
        tmp_path / "frontier.bloom",
        logger=server.logger,
        bloom_capacity=10_000,
    )
    # 第一次添加：bloom 都未命中
    res1 = f.add_urls(["https://example.test/x", "https://example.test/y"])
    assert res1["bloom_prefilter_hit"] == 0

    # 重复添加：bloom 应预检命中
    res2 = f.add_urls(["https://example.test/x", "https://example.test/y"])
    assert res2["bloom_prefilter_hit"] == 2
    assert res2["skipped"] == 2
    assert res2["added"] == 0
