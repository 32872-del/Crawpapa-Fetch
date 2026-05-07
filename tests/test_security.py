"""测试 save_data 路径穿越防护"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.mcp_client import MCPServerManager
import unified_crawler_server as server


def _is_error(result: str) -> bool:
    try:
        return json.loads(result).get("success") is False
    except json.JSONDecodeError:
        return "错误" in result or "非法" in result or "拒绝" in result


def test_tls_verification_is_default_enabled():
    assert server.VERIFY_TLS is True


def test_request_private_override_can_be_disabled():
    old_value = server.ALLOW_REQUEST_PRIVATE_OVERRIDE
    try:
        server.ALLOW_REQUEST_PRIVATE_OVERRIDE = False
        with pytest.raises(PermissionError):
            server._effective_allow_private(True)
        assert server._effective_allow_private(False) is False
    finally:
        server.ALLOW_REQUEST_PRIVATE_OVERRIDE = old_value


def test_insecure_tls_override_can_be_disabled():
    old_value = server.ALLOW_INSECURE_TLS_OVERRIDE
    try:
        server.ALLOW_INSECURE_TLS_OVERRIDE = False
        with pytest.raises(PermissionError):
            server._effective_verify_tls(False)
        assert server._effective_verify_tls(True) is True
    finally:
        server.ALLOW_INSECURE_TLS_OVERRIDE = old_value


@pytest.fixture(scope="module")
def manager():
    m = MCPServerManager(sys.executable, ["unified_crawler_server.py"])
    m.start()
    yield m
    m.stop()


def test_rejects_dot_dot_traversal(manager):
    result = manager.call_tool_sync(
        "save_data", {"data": "malicious", "filename": "../../etc/passwd"}
    )
    assert _is_error(result)


def test_rejects_absolute_path(manager):
    result = manager.call_tool_sync(
        "save_data", {"data": "malicious", "filename": "/tmp/evil.txt"}
    )
    assert _is_error(result)


def test_rejects_special_characters(manager):
    result = manager.call_tool_sync(
        "save_data", {"data": "malicious", "filename": "file;rm -rf /"}
    )
    assert _is_error(result)


def test_accepts_safe_filename(manager):
    result = manager.call_tool_sync(
        "save_data", {"data": "safe data", "filename": "test_output.json"}
    )
    assert "已保存" in result


def test_rejects_long_filename(manager):
    long_name = "a" * 201 + ".json"
    result = manager.call_tool_sync(
        "save_data", {"data": "data", "filename": long_name}
    )
    assert _is_error(result)
