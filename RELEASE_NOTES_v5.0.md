# Crawler MCP Server v5.0 Release Notes

发布日期：2026-05-06

## 核心变化

v5.0 把项目从“采集工具集合”收敛为“面向 Agent 的采集执行平台”。

推荐主链：

```text
scout_page -> draft_collection_plan -> validate_collection_plan -> execute_collection_plan
```

## 新增能力

- `scout_page`：页面侦察主入口，返回访问诊断、初始状态、菜单候选、链接候选、字段候选、脚本 URL 和推荐计划。
- `draft_collection_plan`：根据页面侦察结果起草 `collection_plan`。
- `validate_collection_plan`：校验计划结构、安全策略和 selector 样本命中。
- `execute_collection_plan`：将计划转为 Pipeline DSL 并执行。
- `extract_initial_state`：从 HTML 脚本里提取初始状态 JSON，可按路径读取。
- `compare_menu_sources`：比较多个菜单来源，输出推荐来源和过滤报告。
- `crawl_list` 0 命中诊断：自动检查 DOM 链接、脚本 URL、challenge、JS shell 和截断信号。
- `crawl_lists` pipeline step：支持多个目录页批量提取详情链接。

## collection_plan

计划现在带审计字段：

```json
{
  "version": "5.0",
  "kind": "collection_plan",
  "assumptions": [],
  "risk_flags": []
}
```

目录采集支持：

```json
{
  "menu_source_path": "__INITIAL_STATE__.navigation.multiBrandMenu[0].mainMenu",
  "category_urls": ["https://example.com/a", "https://example.com/b"]
}
```

## 输出格式

`output_format` 支持：

- `records`
- `dict` / `url_dict`
- `tree` / `by_source`

## 兼容性

- v4.0 底层工具保留。
- 高层入口返回 v5.0 envelope，同时保留旧顶层字段，便于旧调用迁移。

## 测试

当前验证：

```text
91 passed, 1 skipped
```
