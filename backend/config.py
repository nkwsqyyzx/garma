"""Alpha QMT Backend 配置模块。

支持环境变量 + .env 文件加载，pydantic-settings 校验。
"""

from pathlib import Path

from pydantic_settings import BaseSettings
from functools import lru_cache

_BACKEND_DIR = Path(__file__).resolve().parent


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
    """Alpha QMT Backend 配置项。"""

    # QMT-Server 连接
    QMT_SERVER_URL: str = "http://192.168.3.10:8090"
    QMT_SERVER_API_KEY: str = ""
    QMT_SERVER_TIMEOUT: int = 10

    # 功能开关
    QMT_TRADE_ENABLED: bool = True

    # QMT 账户
    QMT_ACCOUNT_ID: str = "666631557962"

    # Redis
    REDIS_URL: str = "redis://192.168.3.80:6379/0"

    # MySQL
    DATABASE_URL: str = "mysql+aiomysql://root:password@127.0.0.1:3306/garma"

    # 服务端口
    BACKEND_PORT: int = 8000

    model_config = {
        "env_file": str(_BACKEND_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
