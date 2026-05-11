"""
全局常量：Redis 键模板、状态码映射、价格类型映射。
两个服务（qmt-market / qmt-trade）共享，零业务逻辑。
"""

# ============================================================
# Redis 键前缀与模板
# ============================================================

# --- 行情相关 ---
KEY_SUB_POOL = "qmt:sub:pool"  # Hash: 订阅池
KEY_SNAPSHOT_TICK = "qmt:snapshot:tick"  # Hash: 最新 Tick 快照
KEY_SNAPSHOT_KLINE = "qmt:snapshot:kline:{period}"  # Hash: 最新 K 线快照
KEY_STREAM_TICK = "qmt:stream:tick:{code}"  # Stream: 单股 Tick
KEY_STREAM_AGG = "qmt:stream:agg"  # Stream: 聚合行情
KEY_STREAM_KLINE = "qmt:stream:kline:{period}:{code}"  # Stream: K 线
KEY_MARKET_LAST_UPDATED = "qmt:market:last_updated"  # String: 最后行情更新时间

# --- 交易相关 ---
KEY_CMD_QUEUE = "qmt:cmd:queue"  # List: 交易命令队列
KEY_CMD_QUEUE_BACKUP = "qmt:cmd:queue:backup"  # List: 消费中备份
KEY_CMD_DLQ = "qmt:cmd:dlq"  # List: 死信队列
KEY_CMD_DELAY_QUEUE = "qmt:cmd:delayqueue"  # ZSet: 延迟重试队列
KEY_ORDER_STATUS = "qmt:order:status:{req_id}"  # String: 单笔委托状态
KEY_EVENT_ORDER_UPDATE = "qmt:event:order_update"  # Stream: 订单回报事件

# --- 账户相关 ---
KEY_ACCOUNT_ASSET = "qmt:account:asset"  # String: 资金快照
KEY_ACCOUNT_POSITIONS = "qmt:account:positions"  # String: 持仓快照
KEY_ACCOUNT_ORDERS = "qmt:account:orders"  # String: 委托快照
KEY_ACCOUNT_TRADES = "qmt:account:trades"  # String: 成交快照
KEY_ACCOUNT_ONLINE = "qmt:account:online:{account_id}"  # String: 账户在线状态

# --- 状态与通知 ---
KEY_MARKET_STATUS = "qmt:market:status"  # String: 行情服务状态
KEY_TRADE_STATUS = "qmt:trade:status"  # String: 交易服务状态
KEY_STATUS_NOTIFY = "qmt:status:notify"  # Pub/Sub: 状态广播
KEY_STATUS_ALERTS = "qmt:status:alerts"  # List: 告警历史

# --- 控制 ---
KEY_KILL_SWITCH = "qmt:kill_switch"  # String: 熔断开关
KEY_CONFIG_SUB_LIMIT = "qmt:config:sub_limit"  # String: 最大订阅数

# ============================================================
# 订单状态映射（xtquant → 系统规范）
# ============================================================

ORDER_STATUS_MAP = {
    50: "PENDING",
    55: "SUBMITTED",
    56: "CANCELING",
    57: "PARTIALLY_CANCELLED",
    58: "CANCELLED",
    59: "PARTIALLY_FILLED",
    60: "FILLED",
    61: "CANCEL_FAILED",
    -1: "REJECTED",
}

# 可撤状态集合
CANCELABLE_STATUSES = {"SUBMITTED", "PARTIALLY_FILLED", "PENDING"}

# 终态集合
FINAL_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "CANCEL_FAILED", "PARTIALLY_CANCELLED"}

# ============================================================
# 价格类型映射（Alpha 传入 → xtquant price_type）
# ============================================================

PRICE_TYPE_MAP = {
    "limit": 11,  # FIX_PRICE 限价委托
    "market": 5,  # MARKET_PEER_PRICE_FIRST 五档撮合转限
    "best5": 6,  # MARKET_BEST_PRICE 对手方最优价
    "cancel_remain": 10,  # MARKET_CANCEL_REMAIN 五档成交剩余撤单
}


# ============================================================
# HTTP API 错误码
# ============================================================

class ErrorCode:
    SUCCESS = 0
    PARAM_ERROR = 1001
    MARKET_NOT_CONNECTED = 1002
    TRADE_NOT_CONNECTED = 1003
    ACCOUNT_NOT_LOGGED_IN = 1004
    SUBSCRIBE_LIMIT_EXCEEDED = 1005
    RISK_REJECTED = 2001
    ORDER_FAILED = 2002
    INTERNAL_ERROR = 5000


# ============================================================
# 组件状态等级
# ============================================================

class StatusLevel:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


# ============================================================
# 告警阈值
# ============================================================

TICK_STALE_SECONDS = 60  # 行情超过 60s 无推送 → degraded
TICK_OFFLINE_SECONDS = 120  # 超过 120s → offline
REDIS_LATENCY_WARN_MS = 100  # Redis 延迟超过 100ms → degraded
STATUS_TTL_SECONDS = 35  # Redis 状态键 TTL（比上报周期多 5s）
ALERT_HISTORY_MAX = 200  # 告警历史最多保留条数
