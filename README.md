# Crawler MCP Server v5.2

面向 LLM/Agent 的网页采集侦察 MCP。它的定位不是替代你的正式采集框架，而是在写采集代码之前，帮助 Agent 更快判断：

- 页面能否合规访问，失败原因是什么。
- 应该使用 requests、curl_cffi、browser、代理还是授权 Cookie。
- 商品列表、目录、字段 selector、初始状态 JSON、脚本 URL、公开 API 线索在哪里。
- 如何生成稳定的 `collection_plan` 或导出给采集框架使用的 site spec。

推荐主链：

```text
probe_access_strategy
  -> observe_browser_network
  -> infer_pagination_strategy
  -> analyze_detail_samples
  -> scout_page
  -> draft_collection_plan
  -> validate_collection_plan
  -> execute_collection_plan
```

如果只是做前置分析，通常跑到 `validate_collection_plan` 或 `export_site_spec_to_spider` 就够了。

## v5.2 核心能力

- 访问穿透诊断：`probe_access_strategy` 对比 `requests/curl_cffi/browser/proxy`，分类 challenge、403、429、JS shell、HTML 截断、API 线索等。
- 浏览器网络观测：`observe_browser_network` 捕获 XHR/fetch/document，识别 JSON/API 候选和翻页参数。
- 翻页识别：`infer_pagination_strategy` 识别 DOM next、query page、offset/cursor 等翻页方式。
- 详情页深度侦察：`analyze_detail_samples` 从列表页抽详情链接，低频进入详情页样本，反推详情字段 selector 和风险。
- 页面业务结构理解：`extract_initial_state` 支持读取 `navigation.multiBrandMenu[0].mainMenu` 这类真实前端状态路径。
- 菜单来源比较：`compare_menu_sources` 输出候选来源、推荐来源、过滤报告和解释。
- 选择器推断：`infer_site_selectors`、`infer_site_spec_from_samples` 生成列表和详情字段候选。
- 0 命中诊断：`crawl_list` 在 selector 0 命中时会检查 DOM、脚本 URL、challenge、JS shell 和截断信号。
- 计划化输出：高层入口统一返回 `ok/version/data/diagnostics/recommendations`，方便 Agent 读取。
- 最佳响应选择：`fetch_best_page` 会并行式比较 `requests/curl_cffi/browser` 等模式的响应质量，优先选择包含 JSON-LD、目标 selector、非 challenge 的 HTML。
- 岗位数据治理：`normalize_job_records` 将招聘记录标准化为分析 schema，输出薪资拆分、地点归一、描述去噪和 A/B/C/D 质量分级。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,dev]"
.\.venv\Scripts\playwright.exe install chromium
.\.venv\Scripts\python.exe setup_mcp_clients.py
```

启动：

```powershell
.\.venv\Scripts\python.exe unified_crawler_server.py
```

验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 常用工具

访问与穿透分析：

- `probe_access_strategy`
- `fetch_best_page`
- `observe_browser_network`
- `infer_pagination_strategy`
- `analyze_detail_samples`
- `diagnose_access_strategy`
- `fetch_page`
- `fetch_page_browser`
- `scroll_and_load`
- `take_screenshot`
- `set_proxy`

页面理解：

- `scout_page`
- `extract_initial_state`
- `compare_menu_sources`
- `infer_category_tree`
- `infer_site_selectors`
- `infer_site_spec_from_samples`

计划与导出：

- `draft_collection_plan`
- `validate_collection_plan`
- `execute_collection_plan`
- `draft_site_spec`
- `validate_site_spec`
- `export_site_spec_to_spider`

运行与存储：

- `crawl_list`
- `crawl_product`
- `run_crawl_pipeline`
- `fetch_pages_batch`
- `normalize_job_records`
- `save_data`
- `save_to_db`
- `frontier_*`

## 合规边界

本项目用于公开页面的合规采集分析，不提供验证码破解、账号风控绕过或未授权访问能力。遇到 challenge/captcha/login wall 时，工具会建议使用授权 Cookie、公开 API、降低频率、人工确认或放弃采集。

## 文档

- [Quickstart](docs/QUICKSTART.md)
- [Setup](docs/SETUP.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Maintenance](docs/MAINTENANCE.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)
- [v5.0 Release Notes](RELEASE_NOTES_v5.0.md)
- [v5.1 Release Notes](RELEASE_NOTES_v5.1.md)
- [v5.2 Release Notes](RELEASE_NOTES_v5.2.md)

## 项目分区

- `crawler_core/`: 可复用采集、解析、诊断、队列和安全模块。
- `unified_crawler_server.py`: 当前 MCP 工具注册与服务入口，后续逐步拆分。
- `tools/`: 操作者脚本和数据任务脚本，不注册为 MCP 工具。
- `workspace/`: 本地实验区和临时工作区。
- `tests/`: 自动化测试；`tests/reports/` 存放人工测试报备。
- `output/`: 生成的 CSV/JSON/HTML/截图等结果文件，不放脚本。
