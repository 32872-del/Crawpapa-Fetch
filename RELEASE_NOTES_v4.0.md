# Crawler MCP Server v4.0 Release Notes

发布日期：2026-05-04

## 新增能力

### 强化反爬
- **三级智能 auto-mode 升级**：dispatcher 现在按 `HTTP→curl_cffi→browser` 顺序自动升级。HTTP 状态 4xx/5xx 失败、或 200 但命中挑战页特征 → 立即升级到下一级。事件日志记录 `escalations` 链路。
- **域名成功模式记忆**：每个域名的成功模式（含 curl_cffi impersonate）会写入 `databases/domain_memory.db`。下次该域名直接走最优路径，省去试错。24h 内未访问或连续 3 次失败会重置。
- **挑战页探测**（`crawler_core/challenge.py`）：Cloudflare、Akamai、hCaptcha、reCAPTCHA、Geetest、Incapsula 多重启发式。`detect_in_response` 综合 status code + headers + cookies + body 判断置信度。
- **可注入挑战特征**：环境变量 `CRAWLER_CHALLENGE_PATTERNS` 用逗号分隔覆盖默认。

### 性能与并发
- **`fetch_pages_batch` 工具**（v4 新）：基于 httpx HTTP/2 的异步并发批抓。最多 200 条 URL，并发 1-50。SSRF/robots/限速策略每条单独检查。h2 缺失时自动降级到 HTTP/1.1，httpx 缺失时退化为线程池 + requests。
- **Bloom filter 性能修复**（`crawler_core/frontier.py`）：dirty flag + 阈值 flush（默认 1 万次 add 或 30 秒一次落盘）。从每批写盘 9.6MB 降到几乎为 0。
- **Bloom 真预检**：`add_urls` 现在用 `might_contain` 跳过已存在 URL 的 SQL roundtrip，返回 `bloom_prefilter_hit` 计数。
- **`frontier_rebuild_bloom` 工具**（v4 新）：从 SQLite 全量重灌 Bloom。

### 安全（P0 修复）
- **DNS rebinding 防护**：`crawler_core/dns_pin.py` 实现 IP pinning。requests 走 url 重写 + Host header 保留；curl_cffi 走原生 `resolve` 参数（仅新版支持，旧版自动降级）；browser 进入前重解析比对。环境变量 `CRAWLER_PIN_DNS=false` 关闭。
- **WHERE LIKE 自动 escape**：用户传入字面量 `%/_` 不再误触模式匹配。LIKE 现在固定为"包含字面量"语义，强制 `ESCAPE '\'` 子句。
- **静默吞异常清理**：HTTPEngine 的 `except Exception: pass` 改成 `logger.warning(..., exc_info=True)`，便于排错；仅在 atexit shutdown 路径保留静默吞（解释器关闭后 stderr 已不可用）。
- **Frontier 多进程租约 CAS**：`lease_token` UUID 抢占。两个 worker 同时 SELECT 后只有一个能 UPDATE 成功，防止重领。

### 解析增强
- **`parse_html_advanced` 工具**（v4 新）：`selector_type` 支持 `css | xpath | jsonpath`。CSS 仍走 BeautifulSoup；XPath 走 parsel/lxml；JSONPath 优先 jsonpath-ng（标准 RFC 9535），退化到 dot-path（兼容主项目原行为）。

### 观测
- **`escalations` 字段**：`fetch` 事件日志现在记录每次 auto-mode 升级（如 `curl_cffi->browser:challenge=cf-challenge`）。
- **`domain_memory_stats` / `domain_memory_reset` 工具**（v4 新）：查看/清空域名记忆。

## 兼容性

- 所有 v3.5 工具签名向后兼容
- 新增工具：`fetch_pages_batch`、`parse_html_advanced`、`frontier_rebuild_bloom`、`domain_memory_stats`、`domain_memory_reset`
- 新增环境变量：`CRAWLER_PIN_DNS`、`CRAWLER_AUTO_MODE_ESCALATION`、`CRAWLER_DOMAIN_MEMORY_ENABLED`、`CRAWLER_BATCH_CONCURRENCY`
- 新增可选依赖：`httpx[http2]`、`anyio`、`parsel`、`jsonpath-ng`（推荐通过 `pip install -e ".[full]"` 一并装）
- frontier.db schema 自动迁移（ALTER TABLE 加 `lease_token` 列）
- bloom 文件大小不匹配时自动 rebuild

## 测试

70 个测试全部通过（v3.5 是 45）：

```bash
pytest -q tests/
# 70 passed, 1 skipped in ~40s
```

新增测试覆盖：DNS pin、LIKE 转义、Bloom dirty/flush、Frontier lease CAS、Bloom rebuild、异步批抓、域名记忆、challenge 探测、多选择器、auto-mode escalation。

## 升级方式

1. 解压 `crawler-mcp-server-v4.0.zip` 到目标目录
2. 运行 `install.bat`（自动安装新增的 httpx + parsel + jsonpath-ng）
3. 重启 Codex / Claude Code / VS Code
4. 旧的 cache/cookies/databases/frontier 数据完全兼容
