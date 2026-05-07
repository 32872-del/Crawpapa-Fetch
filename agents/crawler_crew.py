"""MCP Crawler Cluster - Agent 集群

Agent 通过 MCP Client 调用 MCP Server 的工具（而非本地实现）。
支持多个 MCP Server：基础爬虫 + spider 高级爬虫。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from crewai import Agent, Task, Crew, Process

from config.settings import MIMO_BASE_URL, MIMO_API_KEY, MIMO_MODEL, get_mcp_server_config
from agents.mcp_client import MCPServerManager, make_mcp_tool
from utils.token_monitor import TokenMonitor

load_dotenv()


def _create_tools_from_server(server_name: str, manager: MCPServerManager) -> list:
    """从 MCP Server 创建工具集"""
    tool_defs = {
        "crawler": {
            "fetch_page": ("获取指定 URL 的网页 HTML 内容", {"url": (str, "目标网页 URL"), "headers": (str, "请求头 JSON 字符串")}),
            "parse_html": ("使用 CSS 选择器从 HTML 中提取数据", {"html": (str, "HTML 内容"), "selector": (str, "CSS 选择器")}),
            "extract_links": ("从 HTML 中提取所有链接", {"html": (str, "HTML 内容"), "base_url": (str, "基础 URL")}),
            "extract_text": ("从 HTML 中提取纯文本", {"html": (str, "HTML 内容"), "selector": (str, "CSS 选择器")}),
            "save_data": ("保存数据到文件", {"data": (str, "要保存的数据"), "filename": (str, "文件名")}),
        },
        "spider": {
            "spider_fetch_page": ("使用 spider 框架获取网页，支持浏览器渲染和缓存", {"url": (str, "目标 URL"), "use_browser": (bool, "是否使用浏览器渲染"), "cache": (bool, "是否使用缓存")}),
            "spider_parse_html": ("使用 CSS 选择器提取数据", {"html": (str, "HTML 内容"), "selector": (str, "CSS 选择器")}),
            "spider_extract_links": ("提取所有链接", {"html": (str, "HTML 内容"), "base_url": (str, "基础 URL")}),
            "spider_crawl_list": ("爬取列表页提取商品链接", {"url": (str, "列表页 URL"), "link_selector": (str, "链接 CSS 选择器"), "base_url": (str, "基础 URL"), "use_browser": (bool, "是否浏览器渲染")}),
            "spider_crawl_product": ("爬取产品详情页", {"url": (str, "产品页 URL"), "fields": (str, "字段定义 JSON"), "use_browser": (bool, "是否浏览器渲染")}),
            "spider_save_to_db": ("保存产品数据到 SQLite", {"data": (str, "产品数据 JSON"), "db_name": (str, "数据库名")}),
            "spider_query_db": ("查询数据库", {"db_name": (str, "数据库名"), "limit": (int, "返回条数")}),
        },
    }

    tools = []
    if server_name in tool_defs:
        for tool_name, (desc, params) in tool_defs[server_name].items():
            tools.append(make_mcp_tool(tool_name, desc, params, manager))
    return tools


def create_crew(
    target_url: str,
    target_data: str,
    output_file: str,
    use_spider: bool = False,
    use_browser: bool = False,
):
    """创建并返回配置好的 Crew、Manager 和 TokenMonitor

    Args:
        target_url: 目标 URL
        target_data: 目标数据描述
        output_file: 输出文件名
        use_spider: 是否使用 spider 高级爬虫（带缓存、反爬、浏览器渲染）
        use_browser: 是否使用浏览器渲染（仅 use_spider=True 时有效）
    """

    # 1. 配置 LLM
    llm = ChatOpenAI(
        base_url=MIMO_BASE_URL,
        api_key=MIMO_API_KEY,
        model=MIMO_MODEL,
        temperature=0.3,
    )

    # 2. 启动 MCP Client
    managers = []
    crawler_tools = []
    spider_tools = []

    # 基础爬虫
    server_config = get_mcp_server_config("crawler")
    crawler_manager = MCPServerManager(
        server_command=server_config["command"],
        server_args=server_config["args"],
        env=server_config.get("env"),
    )
    crawler_manager.start()
    managers.append(crawler_manager)
    crawler_tools = _create_tools_from_server("crawler", crawler_manager)

    # Spider 高级爬虫（可选）
    if use_spider:
        try:
            spider_config = get_mcp_server_config("spider")
            spider_manager = MCPServerManager(
                server_command=spider_config["command"],
                server_args=spider_config["args"],
                env=spider_config.get("env"),
            )
            spider_manager.start()
            managers.append(spider_manager)
            spider_tools = _create_tools_from_server("spider", spider_manager)
        except Exception as e:
            print(f"警告：无法启动 spider MCP Server: {e}")
            print("将仅使用基础爬虫")

    # 3. 合并工具
    all_tools = crawler_tools + spider_tools

    # 4. Token 监控
    token_monitor = TokenMonitor()

    # 5. 定义 Agent
    scheduler = Agent(
        role="调度专家",
        goal="分析用户爬取需求，制定最优爬取策略",
        backstory=(
            "你是一位经验丰富的数据工程师，擅长分析网页结构并制定高效的爬取方案。\n"
            "你需要判断：\n"
            "- 简单页面用基础 HTTP 请求\n"
            "- JS 渲染页面用浏览器渲染\n"
            "- 电商列表页用 spider_crawl_list 批量提取链接\n"
            "- 产品详情页用 spider_crawl_product 提取结构化数据\n"
            "- 数据保存到 SQLite 用 spider_save_to_db"
        ),
        llm=llm,
        verbose=True,
    )

    crawler = Agent(
        role="爬虫工程师",
        goal="执行网页请求，获取原始 HTML 内容",
        backstory=(
            "你是一位专业的爬虫工程师，精通 HTTP 协议和反爬策略。\n"
            "你可以使用：\n"
            "- fetch_page: 基础 HTTP 请求\n"
            "- spider_fetch_page: 高级请求（支持浏览器渲染、缓存）\n"
            "- spider_crawl_list: 批量爬取列表页链接\n"
            "- spider_crawl_product: 爬取产品详情"
        ),
        llm=llm,
        tools=[t for t in all_tools if "fetch" in t.name or "crawl" in t.name],
        verbose=True,
    )

    parser = Agent(
        role="数据解析师",
        goal="从 HTML 中提取结构化数据",
        backstory="你擅长使用 CSS 选择器从混乱的 HTML 中提取干净的数据。",
        llm=llm,
        tools=[t for t in all_tools if "parse" in t.name or "extract" in t.name],
        verbose=True,
    )

    storage = Agent(
        role="数据管理员",
        goal="将提取的数据保存到文件或数据库",
        backstory=(
            "你负责数据的持久化存储。\n"
            "- save_data: 保存到 JSON/CSV/TXT 文件\n"
            "- spider_save_to_db: 保存到 SQLite 数据库（电商产品数据）\n"
            "- spider_query_db: 查询已保存的数据"
        ),
        llm=llm,
        tools=[t for t in all_tools if "save" in t.name or "query" in t.name],
        verbose=True,
    )

    # 6. 定义任务
    browser_hint = "（使用浏览器渲染）" if use_browser else ""
    spider_hint = "使用 spider 高级爬虫工具" if use_spider else "使用基础爬虫工具"

    task1 = Task(
        description=(
            "分析以下爬取需求，制定爬取策略：\n"
            "- 目标 URL: {target_url}\n"
            "- 目标数据: {target_data}\n\n"
            f"- 爬虫模式: {spider_hint}{browser_hint}\n\n"
            "请输出：\n"
            "1. 推荐的 CSS 选择器\n"
            "2. 是否需要处理分页\n"
            "3. 是否需要浏览器渲染\n"
            "4. 反爬策略建议"
        ),
        expected_output="一份详细的爬取策略文档",
        agent=scheduler,
    )

    task2 = Task(
        description=(
            "根据调度专家的策略，爬取以下 URL 的内容：\n"
            "URL: {target_url}\n\n"
            f"爬虫模式: {spider_hint}{browser_hint}\n\n"
            "如果是列表页，使用 spider_crawl_list 提取所有链接。\n"
            "如果是详情页，使用 spider_crawl_product 或 fetch_page 获取内容。"
        ),
        expected_output="网页内容或产品数据",
        agent=crawler,
    )

    task3 = Task(
        description=(
            "从爬虫工程师获取的内容中提取目标数据：\n"
            "目标数据: {target_data}\n\n"
            "使用 parse_html、extract_links、extract_text 工具提取结构化数据。"
        ),
        expected_output="提取到的结构化数据（JSON 格式）",
        agent=parser,
    )

    task4 = Task(
        description=(
            "将解析后的数据保存：\n"
            "文件名: {output_file}\n\n"
            "如果是电商产品数据，使用 spider_save_to_db 保存到 SQLite。\n"
            "否则使用 save_data 保存到文件。"
        ),
        expected_output="保存结果确认",
        agent=storage,
    )

    # 7. 组装 Crew
    crew = Crew(
        agents=[scheduler, crawler, parser, storage],
        tasks=[task1, task2, task3, task4],
        process=Process.sequential,
        verbose=True,
    )

    return crew, managers, token_monitor


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://news.ycombinator.com")
    parser.add_argument("--data", default="获取首页所有新闻标题和链接")
    parser.add_argument("--output", default="result.json")
    parser.add_argument("--spider", action="store_true", help="使用 spider 高级爬虫")
    parser.add_argument("--browser", action="store_true", help="使用浏览器渲染")
    args = parser.parse_args()

    crew, managers, token_monitor = create_crew(
        target_url=args.url,
        target_data=args.data,
        output_file=args.output,
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
        print("\n" + "=" * 50)
        print("最终结果:")
        print(result)
        print("\n" + "=" * 50)
        print(token_monitor.get_summary())
    finally:
        for m in managers:
            m.stop()
