"""Alpha QMT Backend 配置模块。

支持环境变量 + .env 文件加载，pydantic-settings 校验。
config.json 作为基础配置来源，环境变量可覆盖。
"""

import json
from pathlib import Path

from pydantic_settings import BaseSettings
from functools import lru_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 读取 config.json
# ---------------------------------------------------------------------------

_config_json: dict = {}
_config_json_path = _PROJECT_ROOT / "config.json"
if _config_json_path.exists():
    with open(_config_json_path, "r", encoding="utf-8") as f:
        _config_json = json.load(f)

_server_cfg = _config_json.get("server", {})
_market_cfg = _config_json.get("market_server", {})
_trade_cfg = _config_json.get("trade_server", {})
_redis_cfg = _config_json.get("redis", {})

_default_host = _server_cfg.get("host", "192.168.1.11")
_default_market_port = _market_cfg.get("port", 3301)
_default_trade_port = _trade_cfg.get("port", 3300)
_default_redis_host = _redis_cfg.get("host", "192.168.1.70")
_default_redis_port = _redis_cfg.get("port", 6379)
_default_redis_db = _redis_cfg.get("db", 0)


# ---------------------------------------------------------------------------
# Redis 键常量
# ---------------------------------------------------------------------------

# Account
KEY_ACCOUNT_ASSET = "qmt:account:asset"
KEY_ACCOUNT_POSITIONS = "qmt:account:positions"
KEY_ACCOUNT_ORDERS = "qmt:account:orders"
KEY_ACCOUNT_TRADES = "qmt:account:trades"

# Status
KEY_MARKET_STATUS = "qmt:market:status"
KEY_TRADE_STATUS = "qmt:trade:status"
KEY_STATUS_NOTIFY = "qmt:status:notify"

# Trading
KEY_CMD_QUEUE = "qmt:cmd:queue"
KEY_EVENT_ORDER_UPDATE = "qmt:event:order_update"
KEY_ORDER_STATUS = "qmt:order:status:{req_id}"
KEY_KILL_SWITCH = "qmt:kill_switch"


class Settings(BaseSettings):
    """Alpha QMT Backend 配置项。

    优先级：环境变量 / .env > config.json 默认值
    """

    # QMT-Server 连接
    QMT_SERVER_URL: str = f"http://{_default_host}:{_default_trade_port}"
    QMT_SERVER_API_KEY: str = ""
    QMT_SERVER_TIMEOUT: int = 10

    # QMT Market 行情服务
    QMT_MARKET_URL: str = f"http://{_default_host}:{_default_market_port}"

    # 功能开关
    QMT_TRADE_ENABLED: bool = True

    # QMT 账户
    QMT_ACCOUNT_ID: str = "666631557962"

    # Redis
    REDIS_URL: str = f"redis://{_default_redis_host}:{_default_redis_port}/{_default_redis_db}"

    # MySQL
    DATABASE_URL: str = "mysql+aiomysql://root:password@127.0.0.1:3306/garma"

    # 服务端口
    BACKEND_PORT: int = 8998

    # 企业微信群机器人
    WECHAT_WEBHOOK_KEY: str = ""

    model_config = {
        "env_file": str(_BACKEND_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
