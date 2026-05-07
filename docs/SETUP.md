# 安装与配置

## 系统要求

- Windows 10/11 或兼容的 Python 环境
- Python 3.10+
- 网络可访问依赖源和 Playwright 浏览器下载源

## 推荐安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[full,dev]"
.\.venv\Scripts\playwright.exe install chromium
.\.venv\Scripts\python.exe setup_mcp_clients.py
```

## 配置文件

`setup_mcp_clients.py` 会生成或更新：

- `.codex/config.toml`
- `.mcp.json`
- `.vscode/mcp.json`

移动项目目录或重建虚拟环境后，重新运行一次：

```powershell
.\.venv\Scripts\python.exe setup_mcp_clients.py
```

## 重要环境变量

常用安全和运行策略：

- `CRAWLER_VERIFY_TLS=true`
- `CRAWLER_RESPECT_ROBOTS=true`
- `CRAWLER_PIN_DNS=true`
- `CRAWLER_AUTO_MODE_ESCALATION=true`
- `CRAWLER_DOMAIN_MEMORY_ENABLED=true`
- `CRAWLER_PERSIST_COOKIES=true`
- `CRAWLER_FETCH_MAX_LENGTH=80000`
- `CRAWLER_BATCH_CONCURRENCY=5`

复制 `.env.example` 为 `.env` 后可以覆盖默认值。

## 运行目录

这些目录由服务运行时使用：

- `output/`：JSON、CSV、截图等输出。
- `cache/`：页面缓存。
- `databases/`：SQLite 数据库。
- `logs/`：运行事件 JSONL。
- `jobs/`：后台任务状态。
- `frontier/`：URL Frontier 队列。
- `schemas/`：表结构定义。
- `templates/`：Pipeline 模板。
- `cookies/`：Cookie profile。

## 常见问题

### MCP 启动失败

先在项目根目录手动运行：

```powershell
.\.venv\Scripts\python.exe unified_crawler_server.py
```

如果缺依赖，重新执行安装命令。

### 浏览器渲染失败

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

### 本地地址被拒绝

默认禁止访问 localhost、内网和保留地址。测试本地服务时需要显式传：

```json
{
  "allow_private": true,
  "respect_robots": false
}
```

### robots.txt 阻止采集

默认遵守 robots。只有在你有明确授权或本地测试时，才把 `respect_robots` 设为 `false`。

### 输出格式怎么选

- `records`：保留完整记录数组。
- `dict`：输出 `{title: url}`。
- `tree`：按来源目录分组。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
