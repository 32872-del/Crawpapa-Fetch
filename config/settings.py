"""集中配置管理"""
import os
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent

# MiMo API 配置
MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "https://platform.xiaomimimo.com/v1")
MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
MIMO_MODEL = os.getenv("MIMO_MODEL", "MiMo-V2.5-Pro")

# MCP Server 配置
MCP_CONFIG_PATH = PROJECT_ROOT / "config" / "mcp_config.json"


def get_mcp_server_config(server_name: str = "crawler") -> dict:
    """从 mcp_config.json 读取指定 server 的配置"""
    with open(MCP_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return config["mcpServers"][server_name]


# 安全配置
OUTPUT_DIR = PROJECT_ROOT / "output"
MAX_FILENAME_LENGTH = 200

# 爬虫配置
FETCH_MAX_LENGTH = int(os.getenv("FETCH_MAX_LENGTH", "50000"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
