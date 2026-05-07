import json
from pathlib import Path

import setup_mcp_clients as setup_clients


def test_claude_project_config_uses_absolute_server_path(tmp_path, monkeypatch):
    root = tmp_path / "crawler"
    root.mkdir()
    (root / "unified_crawler_server.py").write_text("print('ok')", encoding="utf-8")

    monkeypatch.setattr(setup_clients, "ROOT", root)
    target = setup_clients.write_claude_project_config("python")

    config = json.loads(target.read_text(encoding="utf-8"))
    crawler = config["mcpServers"]["crawler"]
    assert Path(crawler["args"][0]).is_absolute()
    assert crawler["args"][0].endswith("unified_crawler_server.py")
    assert crawler["env"]["CRAWLER_DATA_DIR"].endswith(".crawler-data")
    assert crawler["env"]["CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE"] == "false"
    assert crawler["env"]["CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE"] == "false"


def test_codex_config_includes_data_dir_and_safety_overrides(tmp_path, monkeypatch):
    root = tmp_path / "crawler"
    root.mkdir()
    (root / "unified_crawler_server.py").write_text("print('ok')", encoding="utf-8")

    monkeypatch.setattr(setup_clients, "ROOT", root)
    target = setup_clients.write_codex_config("python")
    content = target.read_text(encoding="utf-8")

    assert str(root / "unified_crawler_server.py").replace("\\", "/") in content
    assert "CRAWLER_DATA_DIR" in content
    assert 'CRAWLER_ALLOW_REQUEST_PRIVATE_OVERRIDE = "false"' in content
    assert 'CRAWLER_ALLOW_INSECURE_TLS_OVERRIDE = "false"' in content
