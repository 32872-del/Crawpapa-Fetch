import contextlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import unified_crawler_server as server


class _PolicyHandler(BaseHTTPRequestHandler):
    post_attempts = 0

    def do_GET(self):
        if self.path == "/robots.txt":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f"User-agent: *\nDisallow: /blocked\nSitemap: {host}/sitemap-index.xml\n".encode()
        elif self.path == "/blocked":
            body = b"<html><body>blocked</body></html>"
        elif self.path == "/sitemap.xml":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{host}/nested-sitemap.xml</loc></sitemap>
</sitemapindex>'''.encode()
        elif self.path == "/sitemap-index.xml":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{host}/sitemap-category.xml</loc></sitemap>
  <sitemap><loc>{host}/sitemap-product.xml</loc></sitemap>
</sitemapindex>'''.encode()
        elif self.path == "/sitemap-category.xml":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{host}/health/</loc></url>
  <url><loc>{host}/health/vitamins/</loc></url>
  <url><loc>{host}/health/vitamins/d3/</loc></url>
  <url><loc>{host}/empty/category/</loc></url>
  <url><loc>{host}/advies/editorial/</loc></url>
</urlset>'''.encode()
        elif self.path == "/sitemap-product.xml":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{host}/health/vitamins/d3/product-1.html</loc></url>
  <url><loc>{host}/health/vitamins/product-2.html</loc></url>
</urlset>'''.encode()
        elif self.path == "/nested-sitemap.xml":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{host}/p/1</loc><lastmod>2026-05-03</lastmod></url>
</urlset>'''.encode()
        elif self.path == "/js-shell":
            scripts = "".join(f"<script src='/static/app{i}.js'></script>" for i in range(10))
            body = f"<!doctype html><html><body><div id='app'></div>{scripts}</body></html>".encode()
        elif self.path == "/api-rich":
            body = b'''<!doctype html><html><body>
                <div id="app"></div>
                <script>
                  window.catalogConfig = {
                    "endpoint": "/api/catalog/products?page=1&limit=24",
                    "graphql": "/graphql",
                    "category": "/ajax/category/products?offset=24"
                  };
                </script>
            </body></html>'''
        elif self.path.startswith("/api/catalog/products"):
            body = b'{"items":[{"title":"API Product","price":19.99}],"page":1,"limit":24}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif self.path == "/network-page":
            body = b'''<!doctype html><html><body>
                <div id="app"></div>
                <script>
                  setTimeout(function() {
                    fetch("/api/catalog/products?page=2&limit=24")
                      .then(function(r){ return r.json(); })
                      .then(function(data){ document.body.setAttribute("data-loaded", data.items.length); });
                  }, 100);
                </script>
            </body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif self.path == "/challenge":
            body = b"<!doctype html><html><body><div class='cf-challenge'>captcha</div></body></html>"
        elif self.path == "/large-html":
            body = ("<!doctype html><html><body>" + ("x" * (server.FETCH_MAX_LENGTH + 100)) + "</body></html>").encode()
        elif self.path == "/products":
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<!doctype html><html><body>
                <a rel="next" href="/products?page=2">Next</a>
                <article class="product-card">
                  <a class="product-item-link" href="/p/1">Local Product</a>
                  <img class="product-image-photo" src="{host}/image.jpg">
                  <span class="price">$19.99</span>
                </article>
                <article class="product-card">
                  <a class="product-item-link" href="/p/2">Second Product</a>
                  <span class="price">$29.99</span>
                </article>
            </body></html>'''.encode()
        elif self.path.startswith("/products-page"):
            host = f"http://127.0.0.1:{self.server.server_port}"
            page = "2" if "page=2" in self.path else "1"
            next_link = '<a rel="next" href="/products-page?page=2">Next</a>' if page == "1" else ""
            body = f'''<!doctype html><html><body>
                {next_link}
                <article class="product-card">
                  <a class="product-item-link" href="/detail/a{page}">Detail A</a>
                </article>
                <article class="product-card">
                  <a class="product-item-link" href="/detail/b{page}">Detail B</a>
                </article>
            </body></html>'''.encode()
        elif self.path.startswith("/detail/"):
            slug = self.path.rsplit("/", 1)[-1]
            body = f'''<!doctype html><html><body>
                <h1 class="product-title">Detail Product {slug}</h1>
                <span class="price">$19.{len(slug)}9</span>
                <img class="main-image" src="/images/{slug}.jpg">
                <div class="product-description">Deep detail description for product {slug}.</div>
            </body></html>'''.encode()
        elif self.path == "/script-products":
            body = b'''<!doctype html><html><body>
                <div id="app"></div>
                <script>
                  window.__INITIAL_STATE__ = {
                    "products": [
                      {"url": "/products/sku-1.html", "name": "Script Product"},
                      {"url": "/p/sku-2", "name": "Second Script Product"}
                    ]
                  };
                </script>
            </body></html>'''
        elif self.path == "/menu-state":
            body = b'''<!doctype html><html><body>
                <script>
                  window.__INITIAL_STATE__ = {
                    "navigation": {
                      "mainMenu": [
                        {"title": "Content", "url": "/content", "contentPage": true}
                      ],
                      "multiBrandMenu": [
                        {
                          "brand": "VERO MODA",
                          "mainMenu": [
                            {"title": "Women", "url": "/women", "children": [
                              {"title": "Dresses", "url": "/women/dresses"},
                              {"title": "Hidden", "url": "/women/hidden", "hidden": true},
                              {"title": "Blog", "url": "/blog", "contentPage": true},
                              {"title": "External", "url": "https://example.org", "externalLink": true},
                              {"title": "Dresses", "url": "/women/dresses"}
                            ]},
                            {"title": "Sale", "url": "/sale"}
                          ]
                        }
                      ]
                    }
                  };
                </script>
            </body></html>'''
        elif self.path == "/menu-categories":
            body = b'''<!doctype html><html><body>
                <a class="product-item-link" href="/p/seed">Seed Product</a>
                <script>
                  window.__INITIAL_STATE__ = {
                    "navigation": {
                      "multiBrandMenu": [
                        {"brand": "VERO MODA", "mainMenu": [
                          {"title": "Dresses", "url": "/cat/dresses"},
                          {"title": "Tops", "url": "/cat/tops"}
                        ]}
                      ]
                    }
                  };
                </script>
            </body></html>'''
        elif self.path == "/cat/dresses":
            body = b'''<!doctype html><html><body>
                <a class="product-item-link" href="/p/dress-1">Dress One</a>
            </body></html>'''
        elif self.path == "/cat/tops":
            body = b'''<!doctype html><html><body>
                <a class="product-item-link" href="/p/top-1">Top One</a>
            </body></html>'''
        elif self.path.startswith("/p/"):
            host = f"http://127.0.0.1:{self.server.server_port}"
            body = f'''<!doctype html><html><body>
                <h1 class="page-title title">Local Product</h1>
                <div class="product-description">A sturdy local product for testing.</div>
                <span class="price">$19.99</span>
                <a href="/p/1">Product</a>
                <img class="product-image-photo" src="{host}/image.jpg">
            </body></html>'''.encode()
        else:
            body = b'<!doctype html><html><body><h1 class="title">Local Product</h1><a href="/p/1">Product</a></body></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        type(self).post_attempts += 1
        if self.path == "/flaky-post" and type(self).post_attempts == 1:
            body = b"rate limited"
            self.send_response(429)
            self.send_header("Retry-After", "0")
        else:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@contextlib.contextmanager
def _local_site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PolicyHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_robots_policy_is_enforced():
    with _local_site() as site:
        result = json.loads(server.fetch_page(
            f"{site}/blocked",
            mode="requests",
            use_cache=False,
            allow_private=True,
        ))
        assert result["success"] is False
        assert result["type"] == "fetch_failed"
        assert "robots.txt" in result["message"]


def test_private_network_targets_are_rejected_by_default():
    with _local_site() as site:
        result = json.loads(server.fetch_page(
            site,
            mode="requests",
            use_cache=False,
            respect_robots=False,
        ))
        assert result["success"] is False
        assert result["type"] == "fetch_failed"
        assert "Private/local/reserved" in result["message"]


def test_private_network_targets_can_be_allowed_explicitly():
    with _local_site() as site:
        result = server.fetch_page(
            site,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        )
        assert "Local Product" in result


def test_fetch_events_and_metrics_are_recorded():
    with _local_site() as site:
        server.fetch_page(
            site,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        )

    events = json.loads(server.get_recent_events(limit=20, event_type="fetch", domain="127.0.0.1"))
    assert events["count"] >= 1
    assert events["events"][-1]["success"] is True
    assert events["events"][-1]["domain"].startswith("127.0.0.1")

    metrics = json.loads(server.get_metrics(limit=100))
    assert metrics["fetch_count"] >= 1
    assert metrics["success"] >= 1


def test_post_retries_after_429():
    _PolicyHandler.post_attempts = 0
    with _local_site() as site:
        result = server.fetch_post(
            f"{site}/flaky-post",
            data='{"hello":"world"}',
            respect_robots=False,
            allow_private=True,
        )
    assert json.loads(result)["ok"] is True
    assert _PolicyHandler.post_attempts == 2


def test_parse_sitemap_uses_iterative_depth_limit():
    with _local_site() as site:
        shallow = json.loads(server.parse_sitemap(f"{site}/sitemap.xml", allow_private=True, max_depth=0))
        assert shallow == []

        expanded = json.loads(server.parse_sitemap(f"{site}/sitemap.xml", allow_private=True, max_depth=2))
        assert expanded[0]["url"] == f"{site}/p/1"


def test_scrapling_status_reports_vendored_package():
    status = json.loads(server.scrapling_status())

    assert status["ok"] is True
    assert status["data"]["vendored"] is True
    assert status["data"]["version"] == "0.4.8"
    assert status["data"]["notice_file"] == "THIRD_PARTY_NOTICES.md"
    assert status["diagnostics"]["integration"] == "vendored"


def test_scrapling_parse_css_and_find_similar():
    html = """<!doctype html><html><body>
      <article class="product-card" id="a1"><h3>Alpha</h3><span class="price">$10</span></article>
      <article class="product-card" id="b2"><h3>Beta</h3><span class="price">$12</span></article>
      <article class="product-card" id="c3"><h3>Gamma</h3><span class="price">$14</span></article>
    </body></html>"""

    parsed = json.loads(server.scrapling_parse(
        html,
        selector=".product-card h3",
        selector_type="css",
        url="https://example.test/catalog",
    ))
    similar = json.loads(server.scrapling_find_similar(
        html,
        seed_selector="#a1",
        selector_type="css",
        url="https://example.test/catalog",
        similarity_threshold=0.1,
    ))

    assert parsed["ok"] is True
    assert parsed["data"]["count"] == 3
    assert parsed["data"]["values"][0] == "Alpha"
    assert parsed["data"]["records"][0]["tag"] == "h3"
    assert similar["ok"] is True
    assert similar["data"]["count"] >= 2
    assert any(item["tag"] == "article" for item in similar["data"]["records"])


def test_scrapling_fetch_static_uses_local_html():
    with _local_site() as site:
        result = json.loads(server.scrapling_fetch(
            f"{site}/products",
            mode="static",
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["data"]["status"] == 200
    assert "Local Product" in result["data"]["html"]
    assert result["diagnostics"]["engine"] == "scrapling"


def test_recent_events_tail_reader_does_not_scan_full_file():
    server.EVENT_LOG_FILE.write_text(
        "\n".join(
            json.dumps({"event": "fetch", "domain": f"old-{i}", "success": True})
            for i in range(5)
        )
        + "\n"
        + "\n".join(
            json.dumps({"event": "fetch", "domain": "tail.example", "success": True, "i": i})
            for i in range(3)
        ),
        encoding="utf-8",
    )
    old_limit = server.EVENT_LOG_TAIL_LINES
    server.EVENT_LOG_TAIL_LINES = 3
    try:
        events = server._read_recent_events(limit=10, event_type="fetch")
    finally:
        server.EVENT_LOG_TAIL_LINES = old_limit
    assert len(events) == 3
    assert all(event["domain"] == "tail.example" for event in events)


def test_tail_file_lines_preserves_utf8_tail_lines():
    old_lines = [
        json.dumps({"event": "fetch", "domain": "old.example", "text": "旧数据" * 20}, ensure_ascii=False)
        for _ in range(500)
    ]
    tail_lines = [
        json.dumps({"event": "fetch", "domain": "tail.example", "text": "请完成安全验证"}, ensure_ascii=False),
        json.dumps({"event": "fetch", "domain": "tail.example", "text": "正常中文尾部"}, ensure_ascii=False),
    ]
    server.EVENT_LOG_FILE.write_text("\n".join(old_lines + tail_lines), encoding="utf-8")

    lines = server._tail_file_lines(server.EVENT_LOG_FILE, 2)

    assert len(lines) == 2
    assert "请完成安全验证" in lines[0]
    assert "正常中文尾部" in lines[1]
    assert "\ufffd" not in "".join(lines)


def test_cache_pruning_removes_oldest_files():
    old_limit = server.CACHE_MAX_SIZE_MB
    server.CACHE_MAX_SIZE_MB = 1
    try:
        for i in range(3):
            path = server.CACHE_DIR / f"manual_prune_{i}.json"
            path.write_text(json.dumps({"text": "x" * 700_000}), encoding="utf-8")
            old_time = time.time() - (100 - i)
            os.utime(path, (old_time, old_time))
        server._prune_cache_if_needed()
        remaining = list(server.CACHE_DIR.glob("manual_prune_*.json"))
        assert len(remaining) < 3
    finally:
        server.CACHE_MAX_SIZE_MB = old_limit
        for path in server.CACHE_DIR.glob("manual_prune_*.json"):
            path.unlink(missing_ok=True)


def test_detect_challenge_page_patterns_can_be_disabled():
    old_enabled = server.DETECT_CHALLENGE_PAGES
    try:
        server.DETECT_CHALLENGE_PAGES = True
        assert server._detect_challenge_page("<html><div class='cf-challenge'></div></html>") == "cf-challenge"
        assert server._detect_challenge_page("<html>请完成安全验证</html>") == "请完成安全验证"
        assert server._detect_challenge_page("<html><h1>Normal page</h1></html>") == ""

        server.DETECT_CHALLENGE_PAGES = False
        assert server._detect_challenge_page("<html>captcha</html>") == ""
    finally:
        server.DETECT_CHALLENGE_PAGES = old_enabled


def test_diagnose_crawler_setup_reports_ready_checks():
    report = json.loads(server.diagnose_crawler_setup())
    assert report["version"] == server.SERVER_VERSION
    assert report["summary"]["personal_use_ready"] is True

    checks = {item["name"]: item for item in report["checks"]}
    assert checks["version_alignment"]["status"] == "ok"
    assert checks["dir_data"]["status"] == "ok"
    assert checks["dir_jobs"]["status"] == "ok"
    assert checks["security_tls_verify"]["status"] == "ok"
    assert checks["security_browser_sandbox"]["status"] == "ok"
    assert "security_request_private_override" in checks
    assert "security_insecure_tls_override" in checks


def test_crawl_status_reports_dynamic_tool_count():
    status = json.loads(server.get_crawl_status())
    assert status["tools_count"] == server._registered_tool_count()
    assert status["tools_count"] >= 39
    assert status["paths"]["data_dir"] == str(server.DATA_DIR)
    assert status["config"]["allow_request_private_override"] == server.ALLOW_REQUEST_PRIVATE_OVERRIDE
    assert status["config"]["allow_insecure_tls_override"] == server.ALLOW_INSECURE_TLS_OVERRIDE


def test_crawl_status_marks_unknown_tool_count():
    manager = server.mcp._tool_manager
    try:
        server.mcp._tool_manager = object()
        status = json.loads(server.get_crawl_status())
    finally:
        server.mcp._tool_manager = manager

    assert status["tools_count"] is None
    assert "无法读取 MCP 注册工具数量" in status["warnings"][0]


def test_frontier_tools_and_cookie_profiles(tmp_path):
    old_frontier = server._frontier
    old_cookie_store = server._cookie_store
    try:
        server._frontier = server.URLFrontier(
            tmp_path / "frontier.db",
            tmp_path / "frontier.bloom",
            logger=server.logger,
            bloom_capacity=10_000,
        )
        server._cookie_store = server.CookieStore(tmp_path / "cookies")

        with _local_site() as site:
            added = json.loads(server.frontier_add_urls(
                json.dumps([f"{site}/p/1", f"{site}/p/1"]),
                priority=10,
                kind="detail",
                allow_private=True,
            ))
            assert added["added"] == 1
            assert added["skipped"] == 1

            batch = json.loads(server.frontier_next_batch(limit=1, worker_id="pytest"))
            assert batch["count"] == 1
            assert batch["items"][0]["url"] == f"{site}/p/1"

            done = json.loads(server.frontier_mark_done(json.dumps([batch["items"][0]["id"]])))
            assert done["updated"] == 1

        saved = json.loads(server.save_cookie_profile("example.com", '{"session":"abc"}'))
        assert saved["saved"] is True

        hidden = json.loads(server.get_cookie_profile("example.com"))
        assert hidden["cookies"]["session"] == "***"

        profiles = json.loads(server.list_cookie_profiles())
        assert profiles["profiles"][0]["profile"] == "example.com"

        cleared = json.loads(server.clear_cookie_profile("example.com"))
        assert cleared["removed"] == 1
    finally:
        server._frontier = old_frontier
        server._cookie_store = old_cookie_store


def test_pipeline_with_frontier_and_template_runs_against_local_site(tmp_path):
    old_frontier = server._frontier
    old_template_store = server._template_store
    try:
        server._frontier = server.URLFrontier(
            tmp_path / "frontier.db",
            tmp_path / "frontier.bloom",
            logger=server.logger,
            bloom_capacity=10_000,
        )
        server._template_store = server.TemplateStore(tmp_path / "templates")

        with _local_site() as site:
            pipeline = {
                "mode": "requests",
                "use_cache": False,
                "steps": [
                    {
                        "step": "crawl_list",
                        "url": "{{site}}",
                        "selector": "a",
                        "max_items": 1,
                        "respect_robots": False,
                    },
                    {"step": "frontier_add", "source": "links", "priority": 10, "kind": "detail"},
                    {"step": "frontier_next", "limit": 1, "worker_id": "pytest"},
                    {
                        "step": "crawl_products",
                        "fields": {"title": "h1.title"},
                        "max_items": 1,
                        "respect_robots": False,
                    },
                    {"step": "filter", "condition": "title exists"},
                    {"step": "save_json", "filename": "test_pipeline_products.json"},
                ],
            }

            saved = json.loads(server.save_crawl_template(
                "local_products",
                json.dumps(pipeline),
                "local pipeline",
            ))
            assert saved["saved"] is True

            result = json.loads(server.run_crawl_template(
                "local_products",
                variables=json.dumps({"site": site}),
                allow_private=True,
            ))
            assert result["success"] is True
            assert result["records_count"] == 1
            assert result["sample"][0]["title"] == "Local Product"

            stats = json.loads(server.frontier_stats())
            assert stats["status_counts"]["done"] == 1

        listed = json.loads(server.list_crawl_templates())
        assert listed["templates"][0]["name"] == "local_products"

        loaded = json.loads(server.get_crawl_template("local_products"))
        assert loaded["name"] == "local_products"
    finally:
        server._frontier = old_frontier
        server._template_store = old_template_store


def test_draft_crawl_pipeline_returns_runnable_shape():
    with _local_site() as site:
        result = json.loads(server.draft_crawl_pipeline(
            goal="采集本地商品",
            start_url=site,
            link_selector="a",
            fields='{"title":"h1.title"}',
            output_format="both",
            output_name="test_drafted_pipeline.json",
            max_items=1,
            use_frontier=True,
            allow_private=True,
        ))

    pipeline = result["pipeline"]
    steps = [step["step"] for step in pipeline["steps"]]
    assert steps == ["crawl_list", "frontier_add", "frontier_next", "crawl_products", "save", "save_json"]


def test_site_spec_draft_validate_and_export(tmp_path):
    with _local_site() as site:
        spec_result = json.loads(server.draft_site_spec(
            goal="collect local products",
            start_url=site,
            list_selector="a@href",
            fields=json.dumps({
                "title": "h1.title",
                "price": "h1.title",
                "image_src": "a@href",
            }),
            site="local_products",
            mode="requests",
            wait_selector=".product-item-link",
            render_time=4,
            scroll_count=2,
        ))
        spec = spec_result["spec"]
        assert spec["site"] == "local_products"
        assert spec["list"]["item_link"] == "a@href"
        assert spec["wait_selector"] == ".product-item-link"
        assert spec["sleep_time"] == 4
        assert spec["scroll_count"] == 2

        validation = json.loads(server.validate_site_spec(
            json.dumps(spec),
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))
        assert validation["ok"] is True
        assert validation["summary"]["list_links_found"] == 1
        assert validation["summary"]["field_hits"]["title"] == 1
        assert validation["recommendation"] == "ready_to_export"

    spider_root = tmp_path / "spider_Uvex"
    spider_root.mkdir()
    export_result = json.loads(server.export_site_spec_to_spider(
        json.dumps(spec),
        spider_root=str(spider_root),
    ))
    assert os.path.exists(export_result["spec_path"])
    assert os.path.exists(export_result["runner_path"])

    with open(export_result["spec_path"], encoding="utf-8") as file:
        saved_spec = json.load(file)
    assert saved_spec["site"] == "local_products"
    assert "ConfigSpider" in open(export_result["runner_path"], encoding="utf-8").read()


def test_site_spec_export_versions_and_rollback(tmp_path):
    spider_root = tmp_path / "spider_Uvex"
    spider_root.mkdir()
    spec = {
        "version": "1.0",
        "site": "versioned_site",
        "mode": "requests",
        "start_urls": [{"url": "https://example.test/list"}],
        "list": {"item_link": "a.product@href"},
        "detail": {"title": "h1", "price": ".price", "image_src": "img@src"},
    }

    first = json.loads(server.export_site_spec_to_spider(json.dumps(spec), spider_root=str(spider_root)))
    assert os.path.exists(first["version_path"])

    spec["detail"]["title"] = "h1.product-title"
    second = json.loads(server.export_site_spec_to_spider(json.dumps(spec), spider_root=str(spider_root)))
    assert os.path.exists(second["version_path"])

    versions = json.loads(server.list_site_spec_versions("versioned_site", spider_root=str(spider_root)))
    assert len(versions["versions"]) == 2

    rollback = json.loads(server.rollback_site_spec_version(
        "versioned_site",
        version=os.path.splitext(os.path.basename(first["version_path"]))[0],
        spider_root=str(spider_root),
    ))
    assert rollback["version"] == os.path.splitext(os.path.basename(first["version_path"]))[0]

    with open(spider_root / "site_specs" / "versioned_site.json", encoding="utf-8") as file:
        restored = json.load(file)
    assert restored["detail"]["title"] == "h1"


def test_diagnose_access_strategy_detects_js_shell():
    with _local_site() as site:
        result = json.loads(server.diagnose_access_strategy(
            f"{site}/js-shell",
            target_selector=".product-card",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert "js_rendering_likely_required" in result["findings"]
    assert "target_selector_missed" in result["findings"]
    assert any(item["type"] == "browser_rendering" for item in result["recommendations"])


def test_diagnose_access_strategy_reports_api_hints_and_truncation():
    with _local_site() as site:
        result = json.loads(server.diagnose_access_strategy(
            f"{site}/api-rich",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert "api_hints_found" in result["classification"]["categories"]
    assert result["api_hints"][0]["url"] == f"{site}/api/catalog/products?page=1&limit=24"
    assert any(item["type"] == "api_discovery" for item in result["recommendations"])


def test_probe_access_strategy_classifies_modes_for_agent():
    with _local_site() as site:
        result = json.loads(server.probe_access_strategy(
            f"{site}/api-rich",
            modes="requests",
            include_browser=False,
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["version"].startswith("5.")
    assert result["summary"]["best_mode"] == "requests"
    assert result["summary"]["api_hint_count"] >= 1
    assert result["diagnostics"]["probes"][0]["ok"] is True
    assert any(item["type"] == "preferred_fetch_mode" for item in result["recommendations"])


def test_probe_access_strategy_detects_challenge_without_bypass():
    with _local_site() as site:
        result = json.loads(server.probe_access_strategy(
            f"{site}/challenge",
            modes="requests",
            include_browser=False,
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is False
    assert "challenge" in result["summary"]["categories"]
    assert "challenge" in result["diagnostics"]["probes"][0]["classification"]["categories"]
    assert any(item["type"] == "authorized_session_or_manual_review" for item in result["recommendations"])


def test_observe_browser_network_finds_api_and_pagination_candidates():
    with _local_site() as site:
        result = json.loads(server.observe_browser_network(
            f"{site}/network-page",
            resource_types="xhr,fetch,document",
            render_time=1,
            max_entries=20,
            capture_json_sample=True,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    candidates = result["network"]["candidates"]
    api_candidate = next(item for item in candidates if "/api/catalog/products" in item["url"])
    assert api_candidate["pagination_params"]["page"] == "2"
    assert api_candidate["json_like"] is True
    assert api_candidate["sample_json_keys"] == ["items", "page", "limit"]
    assert any(item["type"] == "network_api_candidate" for item in result["recommendations"])


def test_observe_interactions_records_runtime_actions_and_requests():
    with _local_site() as site:
        result = json.loads(server.observe_interactions(
            f"{site}/network-page",
            render_time=1,
            scroll_count=1,
            click_next=False,
            max_entries=30,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["actions"]
    assert result["interaction_map"]
    assert any("/api/catalog/products" in item["url"] for item in result["network"]["candidates"])
    assert result["interaction_map"][0]["action"] == "scroll"


def test_infer_data_api_finds_item_array_fields_and_pagination():
    with _local_site() as site:
        result = json.loads(server.infer_data_api(
            url=f"{site}/api/catalog/products?page=1&limit=24",
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["api_model"]["item_array"]["path"] == "items"
    assert result["api_model"]["field_paths"]["title"]["path"] == "title"
    assert result["api_model"]["field_paths"]["price"]["path"] == "price"
    assert result["api_model"]["pagination"]["has_pagination"] is True
    assert result["recommendations"][0]["action"] == "implement_api_crawler"


def test_infer_data_api_accepts_candidate_urls_and_ranks_models():
    with _local_site() as site:
        result = json.loads(server.infer_data_api(
            candidate_urls=json.dumps([
                f"{site}/missing-api",
                f"{site}/api/catalog/products?page=1&limit=24",
            ]),
            max_candidates=2,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["api_model"]["source_url"].endswith("/api/catalog/products?page=1&limit=24")
    assert result["diagnostics"]["candidate_count"] == 1
    assert result["diagnostics"]["error_count"] == 1


def test_infer_pagination_strategy_finds_next_link_and_sample_urls():
    with _local_site() as site:
        result = json.loads(server.infer_pagination_strategy(
            f"{site}/products-page",
            mode="requests",
            use_cache=False,
            observe_network_flag=False,
            max_pages=2,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["recommended"]["type"] == "rel_next"
    assert result["sample_next_urls"][0] == f"{site}/products-page?page=2"
    assert any(item["type"] == "sample_pages_next" for item in result["recommendations"])


def test_pagination_params_ignore_page_type_noise():
    params = server._pagination_params_from_url(
        "https://example.com/hz/rhf?currentPageType=Search&currentSubPageType=List&page=2&qid=123"
    )
    assert params == {"page": "2"}
    assert server._has_strong_pagination_params(params) is True
    assert server._has_strong_pagination_params({"currentPageType": "Search"}) is False


def test_analyze_detail_samples_enters_detail_pages_and_infers_fields():
    with _local_site() as site:
        result = json.loads(server.analyze_detail_samples(
            f"{site}/products-page",
            list_selector="a.product-item-link@href",
            target_fields="title,price,image_src,body",
            mode="requests",
            use_cache=False,
            sample_size=2,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["detail_links_found"] == 2
    assert result["sampled_detail_count"] == 2
    detail = result["site_spec"]["detail"]
    assert detail["title"].startswith("h1.")
    assert detail["price"].startswith("span.price")
    assert detail["image_src"].startswith("img.main-image@")
    assert result["samples"][0]["values"]["title"].startswith("Detail Product")
    assert result["risk_flags"] == []


def test_analyze_site_for_crawl_builds_unified_report():
    with _local_site() as site:
        result = json.loads(server.analyze_site_for_crawl(
            f"{site}/products-page",
            goal="product_list",
            fields="title,price,image_src,body",
            modes="requests",
            include_browser=False,
            observe_network_flag=False,
            use_cache=False,
            sample_size=2,
            max_pages=2,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["version"].startswith("5.")
    assert result["summary"]["best_mode"] == "requests"
    assert result["summary"]["list_selector"]
    assert result["summary"]["sampled_detail_count"] == 2
    assert result["site_profile"]["site_type"] == "ecommerce"
    assert result["field_quality"]["overall_grade"] in {"A", "B"}
    assert result["recommended_schema"]["dedupe_keys"]
    assert "# Crawpapa-Fetch Site Analysis Report" in result["markdown_report"]
    assert result["implementation_hints"]["detail_fields"]["title"].startswith("h1.")
    assert result["implementation_hints"]["field_quality_grade"] in {"A", "B"}
    assert result["validation"]["ok"] is True
    assert any(step["name"] == "analyze_detail_samples" and step["ok"] for step in result["steps"])
    assert any(item.get("type") == "implementation_mode" for item in result["recommendations"])


def test_build_site_model_returns_agent_facing_model():
    with _local_site() as site:
        result = json.loads(server.build_site_model(
            f"{site}/products-page",
            goal="product_list",
            fields="title,price,image_src,body",
            modes="requests",
            include_browser=False,
            observe_network_flag=False,
            use_cache=False,
            sample_size=2,
            max_pages=2,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    model = result["site_model"]
    assert model["version"] == "site_model.v1"
    assert model["access"]["access_class"] in {"html_available", "api_hinted_page"}
    assert model["access"]["best_mode"] == "requests"
    assert model["detail_strategy"]["list_selector"]
    assert model["detail_strategy"]["fields"]["title"].startswith("h1.")
    assert model["pagination"]["sample_next_urls"][0].endswith("page=2")
    assert "sample_detail_pages" in model["next_actions"]
    assert result["data"]["site_model"]["crawler_plan"]["mode"] == "requests"


def test_build_site_model_includes_validated_api_model_from_hints():
    with _local_site() as site:
        result = json.loads(server.build_site_model(
            f"{site}/api-rich",
            goal="product_list",
            fields="title,price,image_src,body",
            modes="requests",
            include_browser=False,
            observe_network_flag=False,
            use_cache=False,
            sample_size=1,
            max_pages=1,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    model = result["site_model"]
    assert model["api_model"]["item_array"]["path"] == "items"
    assert model["best_data_source"]["type"] == "api_model"
    assert model["crawler_plan"]["api"]["item_array_path"] == "items"
    assert "implement_api_crawler" in model["next_actions"]


def test_field_quality_report_scores_detail_samples():
    detail_spec = {
        "title": "h1.product-title",
        "price": "span.price",
        "image_src": "img.main-image@src",
        "body": "div.product-description",
    }
    samples = [
        {"values": {
            "title": "Detail Product A",
            "price": "$19.99",
            "image_src": "/images/a.jpg",
            "body": "A useful product description with enough detail for review.",
        }},
        {"values": {
            "title": "Detail Product B",
            "price": "$29.99",
            "image_src": "/images/b.jpg",
            "body": "Another useful product description with enough detail for review.",
        }},
    ]

    result = json.loads(server.field_quality_report(
        detail_spec=json.dumps(detail_spec),
        samples=json.dumps(samples),
        site_type="ecommerce",
        page_type="product_list",
    ))

    assert result["ok"] is True
    assert result["overall_grade"] in {"A", "B"}
    assert any(item["field"] == "price" for item in result["fields"])


def test_detect_site_type_and_generate_markdown_report():
    analysis = {
        "url": "https://example.com/jobs",
        "goal": "agent development jobs with salary and location",
        "fields_requested": ["title", "salary", "location", "requirements"],
        "summary": {"status": "needs_review", "best_mode": "requests", "list_selector": "a.job@href"},
        "scout": {"api_hints": {}, "field_candidates": {"salary": [{"selector": ".salary"}]}},
        "detail_samples": {"samples": [{"values": {"title": "Agent Developer", "salary": "$100k", "location": "Remote"}}]},
        "plan": {"plan": {"fields": {"title": "h1", "salary": ".salary", "location": ".location"}}},
        "steps": [{"name": "probe_access_strategy", "ok": True, "duration_ms": 1}],
    }

    detected = json.loads(server.detect_site_type(analysis_json=json.dumps(analysis)))
    assert detected["site_type"] == "jobs"

    report = server.generate_site_report(json.dumps(analysis))
    assert "# Crawpapa-Fetch Site Analysis Report" in report
    assert "Agent Developer" not in report
    assert "Recommended Schema" in report


def test_prepare_visualization_payload_from_csv_records():
    csv_text = "category,title,heat,release_date,url\nHot,A,100,2026-05-07,https://example.test/a\nHot,B,80,2026-05-08,https://example.test/b\n"

    result = json.loads(server.prepare_visualization_payload(
        records=csv_text,
        input_format="csv",
        dataset_name="hot_search",
        source_url="https://example.test",
    ))

    assert result["ok"] is True
    assert result["dataset"]["name"] == "hot_search"
    assert result["dataset"]["records_count"] == 2
    fields = {item["name"]: item for item in result["schema"]["fields"]}
    assert fields["category"]["role"] == "dimension"
    assert fields["heat"]["role"] == "metric"
    assert fields["url"]["role"] == "metadata"
    assert any(chart["type"] == "bar" for chart in result["suggested_charts"])
    assert result["contract_report"]["status"] == "ok"


def test_prepare_visualization_payload_from_sqlite_table():
    db_name = f"visualization_{int(time.time() * 1000)}"
    rows = [
        {"url": "https://example.test/1", "title": "Helmet A", "price": 219.9, "category": "helmet"},
        {"url": "https://example.test/2", "title": "Helmet B", "price": 299.0, "category": "helmet"},
    ]
    server.save_batch_to_db(json.dumps(rows), db_name=db_name, table="items")

    result = json.loads(server.prepare_visualization_payload(
        db_name=db_name,
        table="items",
        dataset_name="helmet_prices",
        limit=10,
    ))

    assert result["ok"] is True
    assert result["dataset"]["source_type"] == "sqlite"
    assert result["dataset"]["records_count_total"] == 2
    assert result["dataset"]["records_loaded"] == 2
    fields = {item["name"]: item for item in result["schema"]["fields"]}
    assert fields["price"]["role"] == "metric"
    assert result["quality"]["duplicate_rate"] == 0


def test_prepare_visualization_payload_uses_analysis_samples_without_records():
    analysis = {
        "url": "https://example.test/products",
        "goal": "product list",
        "summary": {"best_mode": "requests"},
        "site_profile": {"site_type": "ecommerce", "page_type": "product_list"},
        "field_quality": {"overall_grade": "B"},
        "detail_samples": {
            "samples": [
                {"values": {"title": "Product A", "price": "$19.99", "image_src": "https://example.test/a.jpg"}},
            ]
        },
    }

    result = json.loads(server.prepare_visualization_payload(analysis_json=json.dumps(analysis)))

    assert result["ok"] is True
    assert result["dataset"]["source_type"] == "analysis_report"
    assert result["analysis_context"]["site_type"] == "ecommerce"
    assert result["records_preview"][0]["title"] == "Product A"


def test_validate_visualization_payload_reports_contract_mismatch():
    result = json.loads(server.validate_visualization_payload(json.dumps({"version": "1.0"})))

    assert result["ok"] is False
    assert result["status"] == "fail"
    assert any(issue["severity"] == "error" for issue in result["issues"])


def test_infer_site_selectors_returns_ranked_candidates():
    with _local_site() as site:
        result = json.loads(server.infer_site_selectors(
            f"{site}/products",
            target_fields="list_link,title,price,image_src,body",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["fields"]["list_link"][0]["selector"] == "a.product-item-link@href"
    assert result["fields"]["list_link"][0]["count"] == 2
    assert result["fields"]["price"][0]["selector"].startswith("span.price")
    assert result["fields"]["image_src"][0]["selector"].startswith("img.product-image-photo@")
    assert result["best_spec_fragment"]["list"]["item_link"] == "a.product-item-link@href"


def test_infer_site_spec_from_samples_votes_across_detail_pages():
    with _local_site() as site:
        result = json.loads(server.infer_site_spec_from_samples(
            f"{site}/products",
            goal="collect sampled local products",
            site="sampled_local_products",
            sample_size=2,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    spec = result["spec"]
    assert spec["site"] == "sampled_local_products"
    assert spec["list"]["item_link"] == "a.product-item-link@href"
    assert spec["detail"]["title"].startswith("h1.")
    assert spec["detail"]["price"].startswith("span.price")
    assert spec["detail"]["image_src"].startswith("img.product-image-photo@")
    assert result["fetched_sample_count"] == 2
    assert result["confidence"]["overall"] >= 0.7
    assert result["recommendation"] == "ready_to_validate"


def test_infer_category_tree_discovers_sitemaps_and_filters_empty_categories():
    with _local_site() as site:
        result = json.loads(server.infer_category_tree(
            site,
            max_depth=3,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["discovered"]["robots_sitemap"].endswith("/sitemap-index.xml")
    assert result["discovered"]["category_sitemap"].endswith("/sitemap-category.xml")
    assert result["discovered"]["product_sitemap"].endswith("/sitemap-product.xml")
    assert result["coverage"]["full_product_coverage_likely"] is True
    assert result["empty_candidates_removed"] >= 1
    assert result["categories"][0]["name"] == "Health"
    assert result["categories"][0]["children"][0]["name"] == "Vitamins"
    assert result["categories"][0]["children"][0]["children"][0]["name"] == "D3"


def test_extract_initial_state_reads_multibrand_menu_with_filter_report():
    with _local_site() as site:
        html = server.fetch_page(
            f"{site}/menu-state",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        )
        result = json.loads(server.extract_initial_state(
            html,
            path="navigation.multiBrandMenu[0].mainMenu",
            base_url=site,
            output_format="tree",
            max_depth=3,
        ))

    assert result["matched"] is True
    assert result["source"] == "__INITIAL_STATE__"
    assert result["count"] == 3
    assert result["items"][0]["title"] == "Women"
    assert result["items"][0]["children"][0]["url"] == f"{site}/women/dresses"
    assert result["filter_report"]["hidden"] == 1
    assert result["filter_report"]["contentPage"] == 1
    assert result["filter_report"]["externalLink"] == 1
    assert result["filter_report"]["duplicate"] == 1
    assert result["directory_profile"]["max_depth"] == 2
    assert result["directory_profile"]["url_coverage"] == 1.0
    assert "hierarchical" in result["directory_profile"]["signals"]


def test_compare_menu_sources_recommends_multibrand_main_menu():
    with _local_site() as site:
        html = server.fetch_page(
            f"{site}/menu-state",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        )
        result = json.loads(server.compare_menu_sources(
            html,
            base_url=site,
            paths=json.dumps([
                "navigation.mainMenu",
                "navigation.multiBrandMenu[0].mainMenu",
            ]),
            output_format="summary",
        ))

    assert result["recommended"]["path"] == "navigation.multiBrandMenu[0].mainMenu"
    assert result["recommended"]["count"] == 3
    assert result["recommended"]["directory_profile"]["business_score"] > 0
    assert any("multiBrandMenu" in item for item in result["recommended"]["explanation"])
    assert result["diff_summary"]["available"] is True
    assert result["diff_summary"]["recommended_path"] == "navigation.multiBrandMenu[0].mainMenu"
    assert result["diff_summary"]["compared_source_count"] == 1
    assert result["diff_summary"]["by_source"][0]["path"] == "navigation.mainMenu"
    assert "Women" in result["diff_summary"]["only_in_recommended"]
    assert all("_directory_entries" not in item for item in result["comparisons"])
    assert len(result["comparisons"]) == 2


def test_crawl_list_zero_match_falls_back_to_script_urls_with_diagnostics():
    with _local_site() as site:
        result = json.loads(server.crawl_list(
            f"{site}/script-products",
            link_selector=".missing-product-link",
            base_url=site,
            max_links=5,
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["fallback_used"] == "script_json_url_scan"
    assert result["count"] == 2
    assert result["links"][0]["url"] == f"{site}/products/sku-1.html"
    assert result["diagnostics"]["dom_anchor_count"] == 0
    assert result["diagnostics"]["script_url_count"] == 2
    assert "target_selector_missed" in result["diagnostics"]["findings"]


def test_scout_page_returns_agent_ready_recommendations_and_plan():
    with _local_site() as site:
        result = json.loads(server.scout_page(
            f"{site}/products",
            mode="requests",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))

    assert result["ok"] is True
    assert result["version"].startswith("5.")
    assert "data" in result
    assert "diagnostics" in result
    assert isinstance(result["recommendations"], list)
    assert result["page"]["dom_anchor_count"] == 3
    assert result["recommended_plan"]["list_selector"] == "a.product-item-link@href"
    assert result["recommended_plan"]["version"].startswith("5.")
    assert result["recommended_plan"]["kind"] == "collection_plan"
    assert result["recommended_plan"]["fields"]["price"].startswith("span.price")
    assert any(item["type"] == "list_selector" for item in result["recommendations"])


def test_collection_plan_validate_and_execute_reuses_pipeline():
    with _local_site() as site:
        plan = {
            "start_url": f"{site}/products",
            "mode": "requests",
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
            "list_selector": "a.product-item-link@href",
            "fields": {"title": "h1.title", "price": ".price"},
            "max_items": 2,
            "output": "none",
        }
        validation = json.loads(server.validate_collection_plan(
            json.dumps(plan),
            sample=True,
            allow_private=True,
        ))
        executed = json.loads(server.execute_collection_plan(
            json.dumps(plan),
            allow_private=True,
        ))

    assert validation["ok"] is True
    assert validation["version"].startswith("5.")
    assert "data" in validation
    assert "diagnostics" in validation
    assert validation["sample"]["links_count"] == 2
    assert validation["pipeline"]["steps"][0]["selector"] == "a.product-item-link@href"
    assert executed["success"] is True
    assert executed["version"].startswith("5.")
    assert executed["data"]["records_count"] == 2
    assert executed["links_count"] == 2
    assert executed["records_count"] == 2
    assert executed["sample"][0]["title"] == "Local Product"


def test_draft_collection_plan_feeds_validate_and_execute():
    with _local_site() as site:
        draft = json.loads(server.draft_collection_plan(
            f"{site}/products",
            goal="collect product title and price",
            fields="title,price",
            mode="requests",
            max_items=2,
            output="none",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))
        plan = draft["plan"]
        plan["allow_private"] = True
        plan["respect_robots"] = False
        plan["use_cache"] = False
        validation = json.loads(server.validate_collection_plan(
            json.dumps(plan),
            sample=True,
            allow_private=True,
        ))
        executed = json.loads(server.execute_collection_plan(
            json.dumps(plan),
            allow_private=True,
        ))

    assert draft["recommendation"] == "ready_to_validate"
    assert draft["version"].startswith("5.")
    assert draft["data"]["plan"]["version"].startswith("5.")
    assert draft["data"]["plan"]["kind"] == "collection_plan"
    assert draft["confidence"] >= 0.6
    assert plan["list_selector"] == "a.product-item-link@href"
    assert plan["assumptions"]
    assert isinstance(plan["risk_flags"], list)
    assert set(plan["fields"]) == {"title", "price"}
    assert validation["ok"] is True
    assert validation["sample"]["links_count"] == 2
    assert executed["success"] is True
    assert executed["records_count"] == 2


def test_collection_plan_supports_menu_derived_category_urls():
    with _local_site() as site:
        draft = json.loads(server.draft_collection_plan(
            f"{site}/menu-categories",
            goal="collect products from menu categories",
            fields="title",
            mode="requests",
            max_items=5,
            output="none",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
        ))
        plan = draft["plan"]
        plan["allow_private"] = True
        plan["respect_robots"] = False
        plan["use_cache"] = False
        executed = json.loads(server.execute_collection_plan(
            json.dumps(plan),
            allow_private=True,
        ))

    assert plan["menu_source_path"].endswith("navigation.multiBrandMenu[0].mainMenu")
    assert plan["category_urls"] == [f"{site}/cat/dresses", f"{site}/cat/tops"]
    assert executed["success"] is True
    assert executed["pipeline"]["steps"][0]["step"] == "crawl_lists"
    assert executed["links_count"] == 2
    assert executed["records_count"] == 2
    assert {item["url"] for item in executed["sample"]} == {f"{site}/p/dress-1", f"{site}/p/top-1"}


def test_collection_plan_supports_dict_output_format():
    with _local_site() as site:
        plan = {
            "start_url": f"{site}/products",
            "mode": "requests",
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
            "list_selector": "a.product-item-link@href",
            "fields": {"title": "h1.title"},
            "max_items": 1,
            "output": "none",
            "output_format": "dict",
        }
        executed = json.loads(server.execute_collection_plan(
            json.dumps(plan),
            allow_private=True,
        ))

    assert executed["success"] is True
    assert executed["output_format"] == "dict"
    assert executed["data"]["formatted_sample"] == {"Local Product": f"{site}/p/1"}


def test_proxy_pool_disables_after_consecutive_failures_and_recovers(tmp_path):
    old_proxy_file = server.PROXY_FILE
    proxy_file = tmp_path / "proxy_pool.json"
    proxy_file.write_text(json.dumps({"proxies": [{"host": "proxy.test", "port": 8080}]}), encoding="utf-8")
    try:
        server.PROXY_FILE = proxy_file
        pool = server.ProxyPool()
    finally:
        server.PROXY_FILE = old_proxy_file

    proxy = pool.get_proxy()
    for _ in range(3):
        pool.report_failure(proxy)

    status = pool.get_status()[0]
    assert status["disabled"] is True
    assert pool.get_proxy() is None

    pool._health[0]["disabled_until"] = time.time() - 1
    assert pool.get_proxy() == proxy


def test_proxy_pool_disables_on_low_recent_success_rate(tmp_path):
    old_proxy_file = server.PROXY_FILE
    proxy_file = tmp_path / "proxy_pool.json"
    proxy_file.write_text(json.dumps({"proxies": [{"host": "proxy.test", "port": 8080}]}), encoding="utf-8")
    try:
        server.PROXY_FILE = proxy_file
        pool = server.ProxyPool()
    finally:
        server.PROXY_FILE = old_proxy_file

    proxy = pool.get_proxy()
    for ok in [False, False, True, False, False, True, False, False, True, False]:
        if ok:
            pool.report_success(proxy, 0.05)
        else:
            pool.report_failure(proxy)

    status = pool.get_status()[0]
    assert status["disabled"] is True
    assert status["recent_success_rate"] == 0.3


def test_crawl_job_runs_and_can_resume():
    with _local_site() as site:
        job = json.loads(server.start_crawl_job(
            url=site,
            job_type="crawl_list",
            selector="a",
            base_url=site,
            max_items=5,
            output_name="test_job_links.json",
            use_cache=False,
            respect_robots=False,
            allow_private=True,
            background=False,
        ))
        assert job["status"] == "completed"
        assert job["result"]["count"] == 1
        assert job["result"]["links"][0]["url"].endswith("/p/1")

        status = json.loads(server.get_job_status(job["job_id"]))
        assert status["job_id"] == job["job_id"]
        assert status["status"] == "completed"

        resumed = json.loads(server.resume_job(job["job_id"], background=False))
        assert resumed["status"] == "completed"
        assert resumed["attempts"] == 2

    jobs = json.loads(server.list_jobs(limit=10))
    assert any(item["job_id"] == job["job_id"] for item in jobs["jobs"])


def test_cache_hit_still_enforces_robots_policy():
    with _local_site() as site:
        url = f"{site}/blocked"
        cached = server.fetch_page(
            url,
            mode="requests",
            use_cache=True,
            respect_robots=False,
            allow_private=True,
        )
        assert "blocked" in cached

        result = json.loads(server.fetch_page(
            url,
            mode="requests",
            use_cache=True,
            respect_robots=True,
            allow_private=True,
        ))
        assert result["success"] is False
        assert result["type"] == "fetch_failed"
        assert "robots.txt" in result["message"]


def test_browser_unsafe_flags_are_opt_in():
    launch_args = server._browser_launch_args()
    assert "--no-sandbox" not in launch_args
    assert "--disable-setuid-sandbox" not in launch_args
    assert "--disable-web-security" not in launch_args


def test_spider_compatible_crawl_and_db_round_trip():
    with _local_site() as site:
        links = json.loads(server.spider_crawl_list(
            site,
            "a",
            base_url=site,
            cache=False,
            respect_robots=False,
            allow_private=True,
        ))
        assert links["links"][0]["url"] == f"{site}/p/1"

        data = {
            "url": f"{site}/p/1",
            "handle": "local-product",
            "title": "Local Product",
            "price": 9.99,
            "image_src": [f"{site}/image.jpg"],
        }
        db_name = f"test_spider_{int(time.time() * 1000)}"
        save_result = server.spider_save_to_db(json.dumps(data), db_name=db_name)
        assert "已保存" in save_result

        query_result = json.loads(server.spider_query_db(
            db_name=db_name,
            where=json.dumps({"handle": "local-product"}),
        ))
        assert query_result["total"] == 1
        assert query_result["data"][0]["title"] == "Local Product"


def test_query_db_rejects_raw_sql_where():
    data = {"url": "https://example.test/a", "title": "A"}
    db_name = f"test_where_{int(time.time() * 1000)}"
    server.save_to_db(json.dumps(data), db_name=db_name, table="items")
    result = json.loads(server.query_db(
        db_name=db_name,
        table="items",
        where="title = 'A' OR 1=1",
    ))
    assert result["success"] is False
    assert result["type"] == "query_failed"


def test_registered_schema_validates_required_fields_and_unique_key():
    db_name = f"test_schema_{int(time.time() * 1000)}"
    schema = {
        "columns": {"url": "TEXT", "title": "TEXT", "price": "REAL"},
        "required": ["url", "title"],
        "unique": ["url"],
        "indexes": ["title"],
        "strict": True,
    }

    registered = server.register_table_schema(db_name, "items", json.dumps(schema))
    assert "已注册" in registered

    missing = json.loads(server.save_to_db(
        json.dumps({"url": "https://example.test/item"}),
        db_name=db_name,
        table="items",
    ))
    assert missing["success"] is False
    assert missing["type"] == "db_save_failed"
    assert "缺少必填字段" in missing["message"]

    saved = server.save_to_db(
        json.dumps({"url": "https://example.test/item", "title": "Item", "price": "12.5"}),
        db_name=db_name,
        table="items",
    )
    assert "已保存" in saved

    duplicate = server.save_to_db(
        json.dumps({"url": "https://example.test/item", "title": "Item Copy", "price": 13}),
        db_name=db_name,
        table="items",
    )
    assert "数据已存在" in duplicate


def test_batch_save_is_atomic_by_default():
    db_name = f"test_atomic_{int(time.time() * 1000)}"
    schema = {
        "columns": {"url": "TEXT", "title": "TEXT"},
        "required": ["url", "title"],
        "unique": ["url"],
        "strict": True,
    }
    server.register_table_schema(db_name, "items", json.dumps(schema))
    server.save_to_db(
        json.dumps({"url": "https://example.test/existing", "title": "Existing"}),
        db_name=db_name,
        table="items",
    )

    original_insert = server._insert_record
    calls = {"count": 0}

    def fail_second_insert(cursor, table, record, schema):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("forced insert failure")
        return original_insert(cursor, table, record, schema)

    server._insert_record = fail_second_insert
    try:
        result = json.loads(server.save_batch_to_db(
            json.dumps([
                {"url": "https://example.test/ok", "title": "OK"},
                {"url": "https://example.test/bad", "title": "Bad"},
            ]),
            db_name=db_name,
            table="items",
        ))
        assert result["success"] is False
    finally:
        server._insert_record = original_insert

    query = json.loads(server.query_db(
        db_name=db_name,
        table="items",
        where=json.dumps({"url": "https://example.test/ok"}),
    ))
    assert query["total"] == 0


def test_batch_save_non_atomic_keeps_successful_rows():
    db_name = f"test_non_atomic_{int(time.time() * 1000)}"
    schema = {
        "columns": {"url": "TEXT", "title": "TEXT"},
        "required": ["url", "title"],
        "unique": ["url"],
        "strict": True,
    }
    server.register_table_schema(db_name, "items", json.dumps(schema))

    original_insert = server._insert_record
    calls = {"count": 0}

    def fail_second_insert(cursor, table, record, schema):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("forced insert failure")
        return original_insert(cursor, table, record, schema)

    server._insert_record = fail_second_insert
    try:
        result = json.loads(server.save_batch_to_db(
            json.dumps([
                {"url": "https://example.test/ok", "title": "OK"},
                {"url": "https://example.test/bad", "title": "Bad"},
                {"url": "https://example.test/after", "title": "After"},
            ]),
            db_name=db_name,
            table="items",
            atomic=False,
        ))
    finally:
        server._insert_record = original_insert

    assert result["saved"] == 2
    assert result["errors"] == 1
    assert result["atomic"] is False

    ok = json.loads(server.query_db(db_name=db_name, table="items", where=json.dumps({"url": "https://example.test/ok"})))
    bad = json.loads(server.query_db(db_name=db_name, table="items", where=json.dumps({"url": "https://example.test/bad"})))
    after = json.loads(server.query_db(db_name=db_name, table="items", where=json.dumps({"url": "https://example.test/after"})))
    assert ok["total"] == 1
    assert bad["total"] == 0
    assert after["total"] == 1


def test_score_fetch_candidate_prefers_structured_jobposting():
    structured = """<!doctype html><html><head>
      <script type="application/ld+json">{"@type":"JobPosting","title":"AI Agent Engineer"}</script>
    </head><body><h1>AI Agent Engineer</h1></body></html>"""
    challenge = "<html><body><div class='cf-challenge'>captcha</div></body></html>"

    structured_score = server._score_fetch_candidate(structured)["score"]
    challenge_score = server._score_fetch_candidate(challenge)["score"]

    assert structured_score > challenge_score


def test_normalize_job_records_tool_outputs_analysis_schema():
    records = [{
        "title": "AI智能体开发工程师",
        "location": "大连/远程",
        "salary_or_benefits": "10-15K；五险一金、带薪年假",
        "source_channel": "职坐标",
        "description_requirements": "岗位职责：负责AI智能体系统开发，集成RAG和Prompt能力。职位要求：熟悉Python、LLM、MCP。版权所有 ICP备案信息",
        "url": "https://example.test/job",
        "fetch_status": "parsed_text",
    }]

    result = json.loads(server.normalize_job_records(records=json.dumps(records)))
    row = result["data"]["records"][0]

    assert result["ok"] is True
    assert result["data"]["summary"]["record_count"] == 1
    assert row["title_normalized"] == "AI Agent Engineer"
    assert row["city"] == "大连"
    assert row["is_remote"] is True
    assert row["currency"] == "CNY"
    assert row["salary_min"] == 10000
    assert row["salary_max"] == 15000
    assert "版权所有" not in row["description_clean"]
