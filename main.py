#!/usr/bin/env python3
"""Crawpapa-Fetch command line entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _write_or_print(payload: str, output: str = "") -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        print(f"Wrote {path}")
    else:
        print(payload)


def _compact_json(payload: str) -> str:
    try:
        return json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
    except Exception:
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="crawpapa-fetch",
        description="Agent-oriented crawler analysis MCP server and CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    server_parser = subparsers.add_parser("server", help="Start the MCP server")
    server_parser.add_argument("--name", choices=["crawler", "spider"], default="crawler")

    analyze_parser = subparsers.add_parser("analyze", help="Run a unified pre-crawl site analysis report")
    analyze_parser.add_argument("url")
    analyze_parser.add_argument("--goal", default="product_list")
    analyze_parser.add_argument("--fields", default="title,price,image_src,body")
    analyze_parser.add_argument("--mode", default="auto")
    analyze_parser.add_argument("--modes", default="requests,curl_cffi,browser")
    analyze_parser.add_argument("--list-selector", default="")
    analyze_parser.add_argument("--target-selector", default="")
    analyze_parser.add_argument("--sample-size", type=int, default=3)
    analyze_parser.add_argument("--max-pages", type=int, default=3)
    analyze_parser.add_argument("--max-items", type=int, default=20)
    analyze_parser.add_argument("--output-file", default="")
    analyze_parser.add_argument("--report-format", choices=["json", "markdown"], default="json")
    analyze_parser.add_argument("--no-browser", action="store_true")
    analyze_parser.add_argument("--no-network", action="store_true")
    analyze_parser.add_argument("--allow-private", action="store_true")
    analyze_parser.add_argument("--ignore-robots", action="store_true")

    diagnose_parser = subparsers.add_parser("diagnose", help="Probe access strategy for a URL")
    diagnose_parser.add_argument("url")
    diagnose_parser.add_argument("--target-selector", default="")
    diagnose_parser.add_argument("--modes", default="requests,curl_cffi,browser")
    diagnose_parser.add_argument("--no-browser", action="store_true")
    diagnose_parser.add_argument("--allow-private", action="store_true")
    diagnose_parser.add_argument("--ignore-robots", action="store_true")
    diagnose_parser.add_argument("--output-file", default="")

    subparsers.add_parser("setup-clients", help="Generate MCP client configs")
    subparsers.add_parser("test", help="Run the test suite")

    parser.add_argument("--server", choices=["crawler", "spider"], help=argparse.SUPPRESS)
    parser.add_argument("--test", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.server:
        args.command = "server"
        args.name = args.server
    if args.test:
        args.command = "test"

    if args.command == "server":
        server_script = ROOT / "unified_crawler_server.py"
        return subprocess.run([sys.executable, str(server_script)]).returncode

    if args.command == "analyze":
        import unified_crawler_server as server

        result = server.analyze_site_for_crawl(
            args.url,
            goal=args.goal,
            fields=args.fields,
            modes=args.modes,
            mode=args.mode,
            list_selector=args.list_selector,
            target_selector=args.target_selector,
            sample_size=args.sample_size,
            max_pages=args.max_pages,
            max_items=args.max_items,
            report_format=args.report_format,
            include_browser=not args.no_browser,
            observe_network_flag=not args.no_network,
            respect_robots=not args.ignore_robots,
            allow_private=args.allow_private,
        )
        _write_or_print(_compact_json(result), args.output_file)
        return 0

    if args.command == "diagnose":
        import unified_crawler_server as server

        result = server.probe_access_strategy(
            args.url,
            target_selector=args.target_selector,
            modes=args.modes,
            include_browser=not args.no_browser,
            respect_robots=not args.ignore_robots,
            allow_private=args.allow_private,
        )
        _write_or_print(_compact_json(result), args.output_file)
        return 0

    if args.command == "setup-clients":
        import setup_mcp_clients

        return setup_mcp_clients.main()

    if args.command == "test":
        return subprocess.run([sys.executable, "-m", "pytest", "tests", "-v"]).returncode

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
