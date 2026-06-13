# Crawpapa-Fetch

Crawpapa-Fetch 是一个面向 Agent 的爬虫分析与采集编排 MCP Server。它的核心目标不是替代所有业务爬虫框架，而是把专业爬虫开发中最耗时的“站点侦察、访问策略判断、页面结构分析、选择器验证、API 线索发现、采集计划生成、数据质量检查”暴露成可被 LLM 调用的工具链。

换句话说，它是一个给 AI 使用的 crawler workbench：

```text
natural language request
  -> MCP tools
    -> access probe, browser observation, selector/API inference, sample extraction
      -> crawl plan, site spec, quality report, spider handoff
        -> production crawler framework or controlled MCP execution
```

在传统流程里，开发者需要手动打开 DevTools、复制 HTML、试选择器、观察 XHR、判断是否需要浏览器、写临时脚本、检查样本质量。Crawpapa-Fetch 把这些动作工具化，使 Agent 可以在同一上下文中完成“观察 - 验证 - 修正 - 生成计划”的闭环。

## 当前定位

Crawpapa-Fetch 适合做：

- 爬虫开发前的目标站点建模
- 商品、文章、职位、目录类页面的结构化分析
- 列表页、详情页、分页、站点地图、初始状态 JSON 的侦察
- `requests`、`curl_cffi`、浏览器渲染、代理、授权 Cookie 的访问策略比较
- Product JSON-LD、Open Graph、initial state、XHR/fetch API 的数据源优先级判断
- 采集计划草拟、验证、导出给业务爬虫框架
- 小样本采集、字段质量评分、结果规范化、可视化交接 payload
- Agent 编写生产爬虫代码前的 evidence pack 生成

它不适合做：

- CAPTCHA 破解
- 登录墙绕过
- 未授权数据访问
- 对抗访问控制的 stealth abuse
- 盲目高并发采集

合规边界是项目设计的一部分。工具遇到挑战页、登录要求、访问限制或私网风险时，应报告状态并建议授权 Cookie、官方 API、降速、人工复核或放弃采集。

## 版本状态

- Current version: `5.4.2`
- Primary MCP server: `unified_crawler_server.py`
- Package name: `crawpapa-fetch`
- Python: `>=3.10`
- CLI commands:
  - `crawpapa-fetch`
  - `crawpapa-setup-clients`
  - legacy aliases: `crawler-mcp`, `crawler-setup-clients`

## 技术能力全景

### 1. 访问策略诊断

Crawpapa-Fetch 会把一个 URL 放进多个访问通道里比较，而不是默认相信单次 HTTP 结果。

能力包括：

- `requests` 静态抓取
- `curl_cffi` TLS 指纹请求
- Playwright 浏览器渲染
- 可选代理池
- 授权 Cookie/Profile
- robots.txt 与速率控制
- SSRF/private network guard
- TLS 默认校验
- 失败分类：403、429、challenge、JS shell、短 HTML、空响应、网络错误

关键工具：

```text
probe_access_strategy
fetch_best_page
diagnose_access_strategy
fetch_page
fetch_page_browser
fetch_pages_batch
set_proxy
```

### 2. 页面理解与数据源发现

项目不仅抽 DOM 选择器，也会分析更稳定的数据源。

可识别对象：

- Product/Article/JobPosting 等 JSON-LD
- Open Graph product meta
- frontend initial state JSON
- script URL 与内嵌 API hint
- menu/category tree
- sitemap 与 robots sitemap
- list/detail link pattern
- XHR/fetch JSON endpoint
- query pagination、offset pagination、cursor pagination

关键工具：

```text
scout_page
extract_structured_data
extract_initial_state
compare_menu_sources
infer_category_tree
infer_site_selectors
infer_site_spec_from_samples
observe_browser_network
observe_interactions
infer_data_api
infer_pagination_strategy
analyze_detail_samples
```

### 3. Agent 友好的站点建模

`build_site_model` 是推荐入口。它不是长篇报告，而是给 Agent 编写爬虫代码使用的紧凑模型。

输出重点：

- `access`: 最佳访问模式、失败风险、代理/Cookie 建议
- `best_data_source`: DOM、JSON-LD、Open Graph、API、initial state 的优先级
- `data_sources`: 可用数据源候选及证据
- `interaction_map`: 滚动、点击、翻页、网络请求变化
- `pagination`: 翻页策略与样例 URL
- `category_strategy`: 目录来源、菜单树、站点地图线索
- `detail_strategy`: 详情页字段、样本值、风险标记
- `crawler_plan`: 可执行或可交接的采集计划骨架
- `next_actions`: Agent 下一步应执行的动作

推荐调用：

```text
build_site_model(
  url="https://example.com/products",
  goal="product_list",
  fields="title,price,image_src,body"
)
```

### 4. 采集计划与框架交接

Crawpapa-Fetch 的生产价值不在于“所有站点都用 MCP 直接采完”，而在于把前期不确定性压缩成结构化计划。

你可以选择：

- 停在 `validate_collection_plan`，把证据交给自己的 Scrapy/Playwright/内部框架
- 用 `export_site_spec_to_spider` 导出站点规格
- 用 `scrapling_spider_run` 做受控的小型 spider 运行
- 用 `execute_collection_plan` 做 MCP 内部执行

关键工具：

```text
draft_collection_plan
validate_collection_plan
execute_collection_plan
draft_site_spec
validate_site_spec
export_site_spec_to_spider
scrapling_spider_run
```

### 5. Scrapling 能力内置

仓库内 vendored 了 Scrapling 0.4.8 相关能力，用于提升解析和 spider 运行的韧性。

可用工具：

```text
scrapling_status
scrapling_parse
scrapling_find_similar
scrapling_fetch
scrapling_spider_status
scrapling_spider_run
```

典型用途：

- CSS/XPath 解析
- sibling card 发现
- adaptive selector 存储
- 静态或浏览器抓取
- CrawlSpider/SitemapSpider 风格 JSON 配置运行
- checkpoint、cache、robots、sitemap 检查

### 6. 数据质量与运维观测

采集不仅是拿到 HTML，还要判断结果是否可信。

能力包括：

- 字段完整率与样本质量评分
- 站点类型识别
- 推荐 schema
- job record 规范化
- JSON/SQLite 保存与查询
- 最近事件、轻量指标、运行体检
- 可视化交接 payload 验证

关键工具：

```text
detect_site_type
field_quality_report
generate_site_report
prepare_visualization_payload
validate_visualization_payload
normalize_job_records
save_data
save_to_db
save_batch_to_db
query_db
get_recent_events
get_metrics
diagnose_crawler_setup
```

## 推荐工作流

### Agent 编写爬虫代码

```text
build_site_model
  -> validate_collection_plan
  -> export_site_spec_to_spider
  -> implement crawler in your framework
  -> run sample
  -> inspect quality report
```

### 人工深度分析

```text
probe_access_strategy
  -> fetch_best_page
  -> observe_browser_network
  -> observe_interactions
  -> infer_data_api
  -> infer_pagination_strategy
  -> infer_category_tree
  -> analyze_detail_samples
  -> scout_page
  -> draft_collection_plan
  -> validate_collection_plan
```

### 电商站点建议路径

电商站不要默认从列表页 DOM 开始。更稳定的优先级通常是：

```text
product sitemap
  -> product detail URL
    -> Product JSON-LD / Open Graph product meta
      -> platform variant config
        -> DOM fallback
```

如果详情页已经暴露 Product JSON-LD，例如：

```json
{
  "@type": "Product",
  "name": "...",
  "description": "...",
  "offers": {
    "price": "...",
    "priceCurrency": "..."
  }
}
```

就应该优先解析结构化数据，而不是依赖容易变化的 `.price`、`.product-title`、`.description` class。

## 安装

### Windows

```powershell
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
.\install_portable.bat
.\start.bat
```

手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe setup_mcp_clients.py
.\.venv\Scripts\python.exe -m pytest -q
```

启动 MCP server：

```powershell
.\.venv\Scripts\python.exe unified_crawler_server.py
```

### Linux and macOS

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
chmod +x install.sh start.sh pack.sh
./install.sh
./start.sh
```

手动安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[full,dev]"
.venv/bin/python -m playwright install chromium
.venv/bin/python setup_mcp_clients.py
.venv/bin/python -m pytest -q
```

启动 MCP server：

```bash
.venv/bin/python unified_crawler_server.py
```

更多系统依赖见 [docs/INSTALL_UNIX.md](docs/INSTALL_UNIX.md)。

## CLI 用法

生成站点分析报告：

```bash
crawpapa-fetch analyze https://example.com/products --goal product_list --output-file report.json
crawpapa-fetch analyze https://example.com/products --goal product_list --report-format markdown --output-file report.md
```

诊断访问策略：

```bash
crawpapa-fetch diagnose https://example.com/products
```

启动 server：

```bash
crawpapa-fetch --server crawler
```

## MCP 客户端接入

安装脚本会生成：

```text
.codex/config.toml
.mcp.json
.vscode/mcp.json
```

如果移动项目目录，重新运行：

```bash
crawpapa-setup-clients
```

或：

```bash
python setup_mcp_clients.py
```

## 项目结构

```text
crawler_core/                  reusable crawler engine modules
scrapling/                     vendored Scrapling capabilities
unified_crawler_server.py      MCP tool registration and server entry
agents/                        optional agent orchestration integrations
tools/                         operator scripts and maintenance tools
workspace/                     local experiments and scratch work
tests/                         automated tests
docs/                          setup, tutorial, tool, architecture docs
output/                        generated exports only
cache/ cookies/ databases/
frontier/ jobs/ logs/          runtime state, ignored except .gitkeep
```

运行状态目录默认被 `.gitignore` 忽略，只保留 `.gitkeep`。不要把 `.venv`、cookies、cache、databases、logs、jobs、frontier 推送到公共仓库。

## 打包

Windows：

```powershell
.\pack.bat
```

Linux/macOS：

```bash
./pack.sh
```

等价 Python 命令：

```powershell
.\.venv\Scripts\python.exe tools\maintenance\build_package.py
```

打包产物输出到 `dist/`。打包流程会先执行 secret audit：

```powershell
.\.venv\Scripts\python.exe tools\maintenance\secret_audit.py
```

## 文档

- [Tutorial](docs/TUTORIAL.md)
- [Quickstart](docs/QUICKSTART.md)
- [Tool Guide](docs/TOOL_GUIDE.md)
- [Linux and macOS Installation](docs/INSTALL_UNIX.md)
- [Setup](docs/SETUP.md)
- [Packaging](docs/PACKAGING.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Visualization Handoff](docs/VISUALIZATION_HANDOFF.md)
- [Maintenance](docs/MAINTENANCE.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT. See [LICENSE](LICENSE).
