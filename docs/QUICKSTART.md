# Crawpapa-Fetch v5.3 Quickstart

## 0. Installation

Linux/macOS:

```bash
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
chmod +x install.sh start.sh pack.sh
./install.sh
./start.sh
```

Windows:

```powershell
git clone https://github.com/32872-del/Crawpapa-Fetch.git
cd Crawpapa-Fetch
.\install_portable.bat
.\start.bat
```

See [INSTALL_UNIX.md](INSTALL_UNIX.md) for Linux/macOS details.

## 0.5. One-command analysis

For Agent crawler implementation, start with the site model:

```text
build_site_model(
  url="https://example.com/products",
  goal="product_list",
  fields="title,price,image_src,body"
)
```

Use the model fields directly:

- `site_model.access`
- `site_model.best_data_source`
- `site_model.data_sources`
- `site_model.api_model`
- `site_model.interaction_map`
- `site_model.pagination`
- `site_model.detail_strategy`
- `site_model.crawler_plan`

For human review or longer reports, use the unified analysis command:

```bash
crawpapa-fetch analyze https://example.com/products --goal product_list --output-file report.json
```

Equivalent MCP tool:

```text
analyze_site_for_crawl(
  url="https://example.com/products",
  goal="product_list",
  fields="title,price,image_src,body"
)
```

## 1. 启动前检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe - <<'PY'
import unified_crawler_server as s
print(s.diagnose_crawler_setup())
PY
```

如果需要使用本地 VPN 或代理客户端，先设置本地 HTTP 代理：

```text
set_proxy(proxy_url="http://127.0.0.1:7890", proxy_type="local")
```

不同代理客户端端口不同，请以你的客户端为准。当前 MCP 内部 HTTP 请求更适合先用 HTTP 端口。

## 2. 推荐分析流程

```text
probe_access_strategy(url)
observe_browser_network(url)
observe_interactions(url)
infer_data_api(url or candidate_urls)
infer_pagination_strategy(url)
analyze_detail_samples(url)
scout_page(url)
draft_collection_plan(url, goal, fields)
validate_collection_plan(plan, sample=true)
```

如果你只是让 LLM 写正式采集代码，可以停在 `validate_collection_plan`，把输出里的 selector、API 线索、risk flags 和 recommendations 交给采集框架。

For ecommerce sites, check sitemap and detail-page paths early:

```text
infer_category_tree(url)
parse_sitemap(product_sitemap_url)
analyze_detail_samples(product_detail_or_category_url)
```

If product sitemap coverage is strong and detail pages expose Product JSON-LD, Open Graph product meta, or platform variant config, prefer a sitemap-to-detail crawler over fragile list-page selectors.

## 3. 访问策略探测

```text
probe_access_strategy(
  url="https://example.com/products",
  modes="requests,curl_cffi,browser",
  include_browser=true,
  use_proxy=false,
  target_selector=".product-card"
)
```

重点看：

- `diagnostics.probes`: 每种模式是否成功、HTML 大小、文本量、selector 命中数、失败分类。
- `data.api_hints`: 脚本里发现的 API、商品、目录、翻页 URL。
- `recommendations`: Agent 下一步应该采用的策略。

## 4. 页面结构侦察

网络/API 观测：

```text
observe_browser_network(
  url="https://example.com/products",
  resource_types="xhr,fetch,document",
  wait_selector=".product-card",
  scroll_count=2
)
```

重点看 `network.candidates`、`pagination_params` 和 `recommendations`。

识别翻页：

```text
infer_pagination_strategy(
  url="https://example.com/search?q=kitchen",
  mode="browser",
  wait_selector=".product-card",
  max_pages=3
)
```

进入详情页样本：

```text
analyze_detail_samples(
  url="https://example.com/search?q=kitchen",
  list_selector="a.product-link@href",
  target_fields="title,price,image_src,body",
  mode="browser",
  sample_size=3
)
```

重点看 `site_spec.detail`、`samples.values` 和 `risk_flags`。如果 `body_selector_may_point_to_variant_options` 或 `price_selector_may_include_buybox_payment_text` 出现，说明字段还需要人工/Agent 复核。

```text
scout_page(
  url="https://example.com",
  goal="product_list",
  mode="auto"
)
```

它会返回：

- `menu_candidates`
- `link_candidates`
- `field_candidates`
- `script_url_candidates`
- `api_hints`
- `recommended_plan`

## 5. 目录 JSON 提取

```text
extract_initial_state(
  html=html,
  path="navigation.multiBrandMenu[0].mainMenu",
  base_url="https://example.com",
  output_format="tree"
)
```

比较多个菜单来源：

```text
compare_menu_sources(
  html=html,
  paths='["navigation.mainMenu","navigation.multiBrandMenu[0].mainMenu"]',
  output_format="summary"
)
```

目录分析时重点看：

- `recommended.path`: 推荐使用的菜单源。
- `recommended.directory_profile.business_score`: 业务目录评分。
- `recommended.directory_profile.url_coverage`: 有效 URL 覆盖率。
- `recommended.directory_profile.valid_ratio`: 过滤噪声后的有效比例。
- `recommended.directory_profile.signals`: 层级、目录 URL、低噪声等信号。
- `recommended.explanation`: 为什么这个菜单源比其他候选更适合采集。
- `diff_summary.only_in_recommended`: 推荐源独有的目录。
- `diff_summary.missing_from_recommended`: 其他菜单源有、推荐源没有的目录。
- `diff_summary.by_source[*].url_overlap_ratio`: 推荐源与某个候选源的 URL 重叠率。

## 6. 输出格式

`collection_plan.output_format` 支持：

- `records`: 原始记录数组。
- `dict` / `url_dict`: `{title: url}`，适合 `02_data.json`。
- `tree` / `by_source`: 按目录页或来源分组。
