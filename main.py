#!/usr/bin/env python3
"""MCP Crawler Cluster - 入口文件"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(description="MCP Crawler Cluster")
    parser.add_argument("--run", action="store_true", help="运行 Agent 爬虫集群")
    parser.add_argument(
        "--server", choices=["crawler", "spider"], help="启动指定 MCP Server"
    )
    parser.add_argument("--test", action="store_true", help="运行测试")
    parser.add_argument(
        "--url", default="https://news.ycombinator.com", help="目标 URL"
    )
    parser.add_argument(
        "--data", default="获取首页所有新闻标题和链接", help="目标数据描述"
    )
    parser.add_argument("--output", default="result.json", help="输出文件名")
    parser.add_argument("--spider", action="store_true", help="使用 spider 高级爬虫")
    parser.add_argument("--browser", action="store_true", help="使用浏览器渲染")

    args = parser.parse_args()

    if args.server:
        import subprocess

        server_map = {
            "crawler": "unified_crawler_server.py",
            "spider": "unified_crawler_server.py",
        }
        subprocess.run([sys.executable, server_map[args.server]])
    elif args.run:
        from agents.crawler_crew import create_crew

        crew, managers, monitor = create_crew(
            args.url, args.data, args.output,
            use_spider=args.spider,
            use_browser=args.browser,
        )
        try:
            result = crew.kickoff(
                inputs={
                    "target_url": args.url,
                    "target_data": args.data,
                    "output_file": args.output,
                }
            )
            print(f"\n{'=' * 50}\n结果:\n{result}")
            print(f"\n{'=' * 50}\n{monitor.get_summary()}")
        finally:
            for m in managers:
                m.stop()
    elif args.test:
        import subprocess

        subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v"])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
