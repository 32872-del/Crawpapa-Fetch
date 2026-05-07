"""测试 MCP Client 能否正确连接和调用 MCP Server"""
import sys
import threading
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.mcp_client import MCPServerManager, make_mcp_tool


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><html><body><h1 class="title">Hello MCP</h1><a href="/page1">Link1</a></body></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@pytest.fixture(scope="module")
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def crawler_manager():
    manager = MCPServerManager(
        server_command=sys.executable,
        server_args=["unified_crawler_server.py"],
    )
    manager.start()
    yield manager
    manager.stop()


def test_call_fetch_page(crawler_manager, local_site):
    result = crawler_manager.call_tool_sync(
        "fetch_page",
        {
            "url": local_site,
            "mode": "requests",
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
        },
    )
    assert "Hello MCP" in result


def test_call_get_metrics(crawler_manager, local_site):
    crawler_manager.call_tool_sync(
        "fetch_page",
        {
            "url": local_site,
            "mode": "requests",
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
        },
    )
    result = crawler_manager.call_tool_sync("get_metrics", {"limit": 50})
    assert "fetch_count" in result
    assert "127.0.0.1" in result


def test_call_diagnose_crawler_setup(crawler_manager):
    result = crawler_manager.call_tool_sync("diagnose_crawler_setup", {})
    report = json.loads(result)
    assert report["summary"]["personal_use_ready"] is True
    assert "version_alignment" in {item["name"] for item in report["checks"]}


def test_start_crawl_job_over_mcp(crawler_manager, local_site):
    result = crawler_manager.call_tool_sync(
        "start_crawl_job",
        {
            "url": local_site,
            "job_type": "crawl_list",
            "selector": "a",
            "base_url": local_site,
            "max_items": 5,
            "output_name": "test_mcp_job_links.json",
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
            "background": False,
        },
    )
    job = json.loads(result)
    assert job["status"] == "completed"
    assert job["result"]["count"] == 1
    assert job["result"]["links"][0]["url"].endswith("/page1")


def test_call_parse_html(crawler_manager):
    html = '<html><body><h1 class="title">Hello</h1></body></html>'
    result = crawler_manager.call_tool_sync(
        "parse_html", {"html": html, "selector": "h1.title"}
    )
    assert "Hello" in result


def test_call_extract_links(crawler_manager):
    html = '<html><body><a href="/page1">Link1</a><a href="https://example.com">Link2</a></body></html>'
    result = crawler_manager.call_tool_sync(
        "extract_links", {"html": html, "base_url": "https://test.com"}
    )
    assert "Link1" in result
    assert "https://example.com" in result


def test_call_extract_text(crawler_manager):
    html = "<html><body><script>var x=1;</script><p>Hello World</p></body></html>"
    result = crawler_manager.call_tool_sync(
        "extract_text", {"html": html, "selector": "p"}
    )
    assert "Hello World" in result


def test_call_fetch_page_browser_over_mcp(crawler_manager, local_site):
    pytest.importorskip("playwright.sync_api")
    result = crawler_manager.call_tool_sync(
        "fetch_page_browser",
        {
            "url": local_site,
            "wait_selector": "h1.title",
            "render_time": 0,
            "use_cache": False,
            "respect_robots": False,
            "allow_private": True,
        },
    )
    if "Executable doesn't exist" in result or "playwright install chromium" in result:
        pytest.skip("Playwright browser is not installed")
    assert "Hello MCP" in result
    assert "Playwright Sync API inside the asyncio loop" not in result


def test_make_mcp_tool_creates_crewai_tool(crawler_manager, local_site):
    pytest.importorskip("crewai")
    from crewai.tools import BaseTool

    tool = make_mcp_tool(
        "fetch_page",
        "获取网页",
        {"url": (str, "URL")},
        crawler_manager,
    )
    assert isinstance(tool, BaseTool)
    assert tool.name == "fetch_page"
    result = tool._run(url=local_site, allow_private=True)
    assert "Hello MCP" in result
