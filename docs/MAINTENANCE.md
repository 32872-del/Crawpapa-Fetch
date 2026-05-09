# Maintenance

## 项目定位

这个 MCP 是采集前的侦察与策略工具。新增能力优先服务下面这条主链：

```text
probe_access_strategy -> observe_browser_network -> infer_pagination_strategy -> analyze_detail_samples -> scout_page -> draft_collection_plan -> validate_collection_plan
```

正式全量采集可以交给外部采集框架，MCP 输出 selector、API 线索、目录结构、风险分类和执行建议。

## 文件职责

- `unified_crawler_server.py`: MCP 工具入口、HTTP/browser 引擎、pipeline、collection plan。
- `crawler_core/`: 可复用核心模块，例如缓存、安全、frontier、选择器推断、site spec。
- `tests/`: pytest 测试。新增工具必须补测试。
- `docs/`: 操作者文档。
- `cache/ cookies/ databases/ frontier/ jobs/ logs/ output/ schemas/ templates/`: 运行产物目录。

## 版本同步

升级版本时同步：

- `unified_crawler_server.py` 的 `SERVER_VERSION`
- `pyproject.toml` 的 `version`
- `uv.lock` 中本项目 package 的 `version`
- README 和 release notes

然后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 新增工具原则

优先增强已有高层入口，而不是堆独立工具：

- 能否进入 `probe_access_strategy` 的访问诊断？
- 能否进入 `observe_browser_network` 的网络/API 观测？
- 能否进入 `infer_pagination_strategy` 的翻页识别？
- 能否进入 `analyze_detail_samples` 的详情页字段验证？
- 能否进入 `scout_page` 的页面理解？
- 能否进入 `collection_plan`？
- 能否输出 `diagnostics` 和 `recommendations` 让 Agent 消化？

确实属于底层通用能力时，再新增独立 MCP tool。

## 合规策略

允许增强：

- TLS/browser 模式选择。
- 授权 Cookie profile 复用。
- 代理健康与路径对比。
- JS shell、challenge、403、429、截断、API 线索诊断。
- 网络/API 发现和采集计划建议。

不允许实现：

- 验证码自动破解。
- 登录墙绕过。
- 未授权接口访问。
- 高强度对抗式反风控绕过。

## 发布前检查

- 不提交 `.venv/`、缓存、数据库、日志、cookie、任务状态。
- 不提交真实代理、账号、Cookie、API key。
- 运行 `python tools/maintenance/secret_audit.py`。
- 运行 `pytest -q`。
- 运行 `diagnose_crawler_setup`。
- 运行 `python tools/maintenance/build_package.py` 或 `pack.bat`。
- 确认 README 写明合规边界。

## 2026-05-09 Practical Test Gaps

The 10-site recon test exposed these maintenance priorities:

- API targets need better parse diagnostics. A failed API test should distinguish JSON parse errors, robots denial, 403 responses, and truncated structured content.
- Complex-page `0 hit` results should become diagnostic reports. The report should say whether the likely cause is a weak DOM selector, script-embedded data, truncation, robots, or challenge blocking.
- Output format contracts should be checked before collection starts, especially conflicts such as requesting multi-sheet output while selecting CSV.
- Sites such as `Indeed` and `Bilibili` show useful API hints, but the tool should explain why current selector candidates did not win.
- Sites such as `Pinterest`, `m.weibo.cn`, and `ScrapingCourse Cloudflare` are useful boundary tests. The correct behavior is to report robots/challenge limits clearly, not to keep retrying as if normal collection were available.

## Next Maintenance Targets

- Add `availability_report` to unified analysis output.
- Add `access_class` and `robots_explain` to site scouting output.
- Standardize `0 hit` fallback diagnostics.
- Move output-format conflict checks into the planning stage.
- Continue improving target memory so repeated site analysis reuses previous evidence.
