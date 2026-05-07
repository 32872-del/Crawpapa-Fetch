# Crawpapa-Fetch v5.2 Release Notes

Release date: 2026-05-06

Patch date: 2026-05-07

## 重点

v5.2 新增浏览器网络观测层，目标是让 LLM/Agent 在写采集代码前能看见页面渲染过程中的公开 XHR/fetch/document 请求，并判断哪些请求可能承载列表、详情、翻页或筛选数据。

## 新增能力

- 新增 `fetch_best_page`：
  - 对 `curl_cffi`、`requests`、`browser` 等模式的响应进行质量评分。
  - 优先选择包含 JSON-LD、`JobPosting`、目标 selector 命中的 HTML。
  - 对 challenge、JS shell、短错误响应和失败 JSON 降权。
  - 解决简单 auto 升级可能从优质 HTTP 响应倒退到浏览器挑战页的问题。
- 新增 `normalize_job_records`：
  - 支持 CSV/JSON/本地文件输入。
  - 输出 `title_normalized`、`job_category`、`country`、`province_state`、`city`、`is_remote`。
  - 拆分薪资为 `currency`、`salary_min`、`salary_max`、`salary_period`、`salary_negotiable`、`benefits`。
  - 清洗岗位描述噪声，并输出 A/B/C/D `quality_grade`。
- 新增 `crawler_core/job_normalization.py`，将岗位标准化逻辑从一次性脚本沉淀为可复用核心模块。

- 新增 `observe_browser_network`：
  - 使用 Playwright 渲染页面并监听 response。
  - 捕获 `xhr`、`fetch`、`document` 等资源。
  - 按数据价值打分，识别 JSON/API、翻页参数、状态码、content-type、POST body 预览。
  - 可选抓取小型 JSON 响应样本并返回顶层 key。
- 新增网络候选解释：
  - `network_api_candidate`
  - `pagination_candidate`
  - `json_api_candidate`
  - `dom_extraction`
- 新增 `infer_pagination_strategy`：
  - 识别 DOM `rel=next`、Next 文本链接、query page 参数、offset/cursor 等翻页方式。
  - 过滤 `pageType/currentPageType` 这类页面类型噪声，避免误判为翻页。
  - 输出可低频采样的下一页 URL。
- 新增 `analyze_detail_samples`：
  - 从列表页抽详情链接。
  - 低频进入详情页样本。
  - 对标题、价格、主图、描述等字段投票推断 selector。
  - 输出 `risk_flags`，提示价格/描述是否可能命中购买框、变体或报价噪声。
- 修复 HTTPS + DNS pinning 在 requests 路径上的证书域名误伤。

## 实战样本

Amazon 搜索页测试结果：

- 静态 HTTP 路径可拿到 HTML，但不适合直接抽取完整列表。
- browser 渲染后可命中商品 DOM。
- 网络观测发现若干 XHR 候选，但搜索结果主体仍更适合从渲染 DOM 抽取。
- 翻页识别可以正确推荐真实 Next 链接。
- 详情页小样本能进入商品详情页并识别标题、价格、主图；描述字段在 Amazon 上容易误命中变体/购买框区域，已通过 `risk_flags` 暴露。
- 已低频抓取前三页搜索结果，生成前 100 个商品 CSV。

输出文件：

```text
output/amazon_kitchen_dining_top100.csv
output/amazon_kitchen_dining_top100_summary.json
output/amazon_network_observe.json
output/amazon_pagination_strategy.json
output/amazon_detail_sample_analysis.json
```

## 验证

当前单模块验证：

```text
101 passed, 1 skipped
```

全量验证请运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
