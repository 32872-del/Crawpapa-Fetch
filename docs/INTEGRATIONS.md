# MCP 客户端接入

本项目以本地 stdio MCP Server 方式接入，入口是：

```powershell
.\.venv\Scripts\python.exe unified_crawler_server.py
```

## 自动生成配置

```powershell
.\.venv\Scripts\python.exe setup_mcp_clients.py
```

会生成：

- `.codex/config.toml`
- `.mcp.json`
- `.vscode/mcp.json`

注意：`.codex/config.toml` 和 `.mcp.json` 包含本机绝对路径，默认不建议提交到公开仓库。开源仓库里可参考：

- `docs/codex.config.example.toml`
- `docs/mcp.example.json`

## Codex

使用 `.codex/config.toml`。如果客户端没有自动读取项目级配置，可以把其中的 `[mcp_servers.crawler]` 复制到用户级 Codex 配置。

推荐 Agent 调用顺序：

```text
scout_page
draft_collection_plan
validate_collection_plan
execute_collection_plan
```

## Claude Code

Claude Code 使用项目根目录的 `.mcp.json`。进入项目后运行：

```text
/mcp
```

首次加载项目级 MCP 时需要确认信任。

## VS Code

VS Code/Copilot 使用 `.vscode/mcp.json`。打开项目后在命令面板中执行：

```text
MCP: List Servers
```

需要重新读取工具时执行：

```text
MCP: Reset Cached Tools
```

## 排错

1. 工具不显示：重新运行 `setup_mcp_clients.py`，然后重启客户端。
2. Server 启动失败：手动运行 `.\.venv\Scripts\python.exe unified_crawler_server.py` 查看错误。
3. 路径变更：移动项目目录后必须重新生成 MCP 配置。
4. 虚拟环境变更：重建 `.venv` 后重新生成 MCP 配置。
