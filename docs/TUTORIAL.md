# Crawpapa-Fetch Tutorial

这份文档面向两类人：

- 想用自然语言快速分析一个网站，再让 Agent 写爬虫的人
- 已经有自己的业务爬虫框架，希望用 MCP 缩短调试周期的人

Crawpapa-Fetch 的正确心智模型是：先把目标站点变成有证据的 `site model`，再决定用 DOM、JSON-LD、API、sitemap、browser 还是授权 Cookie 去采。

## 1. 从安装到可用

Windows：

```powershell
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
.\install_portable.bat
.\start.bat
```

Linux/macOS：

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
chmod +x install.sh start.sh pack.sh
./install.sh
./start.sh
```

如果 `start` 检查通过，重启你的 MCP 客户端，例如 Codex、Claude Code 或 VS Code。

验证提示词：

```text
查看 Crawpapa-Fetch 的 crawler status，并告诉我当前可用的访问模式。
```

## 2. 第一条自然语言任务

你可以直接这样问 Agent：

```text
用 Crawpapa-Fetch 分析这个商品页：
https://example.com/product/123

提取 title、price、currency、description、image_src。
优先判断页面里有没有 Product JSON-LD 或 Open Graph product meta。
不要直接假设 DOM 选择器。
```

Agent 应该优先使用：

```text
fetch_best_page
extract_structured_data
analyze_detail_samples
```

如果页面直接暴露 Product JSON-LD，字段优先级通常是：

```text
title       -> Product.name
price       -> Product.offers.price
currency    -> Product.offers.priceCurrency
description -> Product.description
image_src   -> Product.image
```

DOM 选择器只作为 fallback。

## 3. 推荐的 Agent 工作流

### 3.1 一键建模

这是最适合让 Agent 写爬虫前调用的工具：

```text
build_site_model(
  url="https://example.com/products",
  goal="product_list",
  fields="title,price,image_src,body"
)
```

你需要重点看：

```text
site_model.access
site_model.best_data_source
site_model.data_sources
site_model.pagination
site_model.category_strategy
site_model.detail_strategy
site_model.crawler_plan
site_model.next_actions
```

如果 `best_data_source` 是 `json_ld` 或 `api`，不要急着写 DOM 选择器。

### 3.2 手动分阶段分析

如果你希望每一步都可控，按下面顺序：

```text
probe_access_strategy
  -> fetch_best_page
  -> observe_browser_network
  -> observe_interactions
  -> infer_data_api
  -> infer_pagination_strategy
  -> infer_category_tree
  -> analyze_detail_samples
  -> draft_collection_plan
  -> validate_collection_plan
```

每一步解决一个问题：

| 阶段 | 解决的问题 |
| --- | --- |
| `probe_access_strategy` | requests 是否够用，是否需要 curl_cffi/browser/proxy/cookie |
| `fetch_best_page` | 哪种响应质量最高 |
| `observe_browser_network` | 是否有 JSON API 或隐藏分页接口 |
| `observe_interactions` | 滚动、点击、load more 后是否出现新数据 |
| `infer_data_api` | API 响应里的 item array 和字段路径 |
| `infer_pagination_strategy` | page/offset/cursor/next link 怎么走 |
| `infer_category_tree` | 目录从 menu、initial state 还是 sitemap 来 |
| `analyze_detail_samples` | 详情页字段是否稳定 |
| `draft_collection_plan` | 生成采集计划 |
| `validate_collection_plan` | 检查计划风险和缺口 |

## 4. 电商站实战套路

电商站点最常见的错误是直接写：

```text
.product-card
.price
.description
```

这种写法在小样本可能可用，但长期不稳定。更专业的判断顺序应该是：

```text
1. robots/sitemap 是否暴露 product sitemap
2. product detail 是否有 Product JSON-LD
3. 是否有 Open Graph product meta
4. 是否有 platform config，例如 Magento swatch config、variant JSON
5. 列表页是否只是装饰或推荐流
6. DOM 选择器作为最后 fallback
```

### 示例提示词

```text
分析这个电商分类页：
https://example.com/category/skincare

请完成：
1. 判断 requests/curl_cffi/browser 哪种访问方式最稳定
2. 查找 sitemap 和 product detail URL 线索
3. 判断详情页是否有 Product JSON-LD
4. 如果有 Product JSON-LD，给出字段映射
5. 如果没有，给出 DOM fallback 选择器
6. 输出一个可交给生产爬虫框架的采集计划
```

Agent 推荐调用：

```text
probe_access_strategy
infer_category_tree
parse_sitemap
analyze_detail_samples
draft_collection_plan
validate_collection_plan
```

## 5. API 优先的站点

如果页面是 JS 渲染，DOM 里只有空壳，不要马上开浏览器全量跑。先观察网络：

```text
observe_browser_network(
  url="https://example.com/search?q=shoes",
  resource_types="xhr,fetch,document",
  scroll_count=2
)
```

拿到候选 API 后：

```text
infer_data_api(candidate_urls="...")
```

重点看：

```text
api_model.item_array.path
api_model.field_paths
api_model.pagination
recommendations
```

如果 API 稳定，生产爬虫通常应该直接请求 API，而不是浏览器渲染 HTML。

## 6. 列表页到详情页

当目标是列表页采集，先验证列表链接：

```text
analyze_detail_samples(
  url="https://example.com/products",
  list_selector="a.product-link@href",
  target_fields="title,price,image_src,body",
  mode="browser",
  sample_size=3
)
```

判断标准：

- `sample_size` 至少 3
- 每个样本都应有 title
- price 应可转成数字
- image_src 应是产品图片，不是 logo、icon、tracking pixel
- description/body 不应抓到整页导航、付款说明、推荐商品
- 如果字段来自 JSON-LD，记录 JSON path
- 如果字段来自 DOM，记录 selector 与 fallback selector

## 7. 生成采集计划

```text
draft_collection_plan(
  url="https://example.com/products",
  goal="collect product title, price, image, description",
  fields="title,price,image_src,body"
)
```

然后验证：

```text
validate_collection_plan(plan="...", sample=true)
```

计划通过后，有三种去向。

### 7.1 交给自己的生产框架

这是最推荐的方式。Crawpapa-Fetch 输出证据和规则，你的框架负责：

- 长期任务调度
- 断点续爬
- 增量更新
- 数据清洗
- 入库
- 导出
- 监控报警

### 7.2 导出站点规格

```text
export_site_spec_to_spider(spec="...")
```

适合把分析结果转成 spider skeleton 或配置。

### 7.3 MCP 内部小规模执行

```text
execute_collection_plan(plan="...")
```

适合验证计划，不建议把它当成所有生产任务的唯一执行器。

## 8. 与业务爬虫框架协作

如果你已有类似 `spider_Uvex` 的框架，建议分工如下：

| 层级 | 负责内容 |
| --- | --- |
| Crawpapa-Fetch | 站点分析、访问诊断、选择器/API 验证、样本质量检查 |
| 业务爬虫框架 | 正式采集、缓存、去重、字段校验、入库、导出、任务复跑 |
| Agent | 根据 MCP 证据修改/生成站点适配器 |

自然语言流程可以是：

```text
分析 https://example.com/category。
先用 Crawpapa-Fetch 找出最稳数据源和字段映射。
然后按我的业务爬虫框架生成一个站点适配器。
先跑 5 条样本，检查 title/price/image_src/body 完整率。
```

这比“直接让 AI 看网页然后猜选择器”更可靠，因为 Agent 可以调用工具验证每个判断。

## 9. 数据质量检查

采集样本后，建议运行：

```text
field_quality_report
normalize_job_records
generate_site_report
prepare_visualization_payload
validate_visualization_payload
```

典型检查项：

- title 空值率
- price 是否可数值化
- image 是否为产品图
- body 是否污染了导航/推荐/支付说明
- duplicate key 是否稳定
- currency、availability、sku、gtin 是否可补齐
- 样本是否覆盖多个分类或分页

## 10. 常见判断

### requests 成功，browser 也成功，选哪个？

优先选 requests 或 curl_cffi。浏览器更重，只在 JS 渲染、交互加载、授权态依赖明显时使用。

### DOM 有价格，JSON-LD 也有价格，选哪个？

优先 JSON-LD，但要抽样确认它不是过期价格。生产上可以设置 DOM fallback 或交叉校验。

### 列表页能抓到商品，为什么还要看 sitemap？

列表页常受排序、个性化、地区、库存、A/B 测试影响。product sitemap 到 detail 的路径通常更完整、更稳定。

### API 能用，为什么还要保留浏览器观察？

浏览器观察用于发现 API 和确认字段来源。正式采集可以直接请求 API。

### 什么时候停止自动分析，转人工？

出现这些情况时应该停下来复核：

- CAPTCHA 或明确 challenge
- 登录墙
- 价格依赖地区或会员态
- 商品字段必须点选规格才出现
- 页面强依赖加密参数
- 数据来自用户隐私或非公开接口

## 11. 最小可复用提示词

复制下面这段给 Agent：

```text
使用 Crawpapa-Fetch 分析目标站点：{url}

目标字段：{fields}
目标类型：{goal}

要求：
1. 先运行 build_site_model。
2. 不要直接猜 DOM selector。
3. 优先判断 JSON-LD、Open Graph、initial state、XHR API、sitemap。
4. 如果需要 DOM selector，必须用样本验证命中数量和值。
5. 输出推荐访问模式、数据源优先级、字段映射、分页策略、风险点。
6. 最后生成可交给生产爬虫框架的采集计划。
```

示例：

```text
使用 Crawpapa-Fetch 分析目标站点：https://example.com/products

目标字段：title, price, currency, image_src, description, sku
目标类型：product_list
```

## 12. 下一步

阅读：

- [Tool Guide](TOOL_GUIDE.md)
- [Quickstart](QUICKSTART.md)
- [Project Structure](PROJECT_STRUCTURE.md)
- [Visualization Handoff](VISUALIZATION_HANDOFF.md)
- [Security](../SECURITY.md)
