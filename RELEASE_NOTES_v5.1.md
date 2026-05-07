# Crawpapa-Fetch v5.1 Release Notes

Release date: 2026-05-06

## 重点

v5.1 的核心目标是提升“穿透和解析能力”，但保持工具定位：它是给 LLM/Agent 做采集前侦察和策略制定，不是验证码破解器，也不是最终采集框架。

## 新增能力

- 新增 `probe_access_strategy`：
  - 对比 `requests`、`curl_cffi`、`browser` 和可选代理路径。
  - 输出每个模式的 HTML 大小、文本量、脚本数、DOM 链接数、selector 命中数、耗时和失败分类。
  - 分类 challenge、403、429、登录墙、区域限制、超时、TLS 错误、JS shell、HTML 截断、API 线索等。
- 增强 `diagnose_access_strategy`：
  - 新增 `classification`、`api_hints`、`truncated_likely`。
  - recommendations 更适合 Agent 直接制定采集策略。
- 增强 `scout_page`：
  - 输出 `api_hints`。
  - 在缺少列表 selector 时，会把 API/翻页/商品接口线索写入 plan 的 strategy notes。
- 代理策略说明：
  - 对本地 HTTP 代理端口给出可选建议，例如 `http://127.0.0.1:8800`。
  - 不默认强制走代理，避免把用户本地环境写死。

## 验证

当前测试结果：

```text
95 passed, 1 skipped
```

## 5.2 方向

下一阶段建议重点做浏览器网络观测：

- 捕获 XHR/fetch/GraphQL 请求摘要。
- 识别翻页参数：page、offset、limit、cursor、sort、filter。
- 生成 API-first 的采集计划草案。
- 给代理做可用性测试和站点级策略记忆。
