"""交易时间判断工具。"""

from datetime import time

# A 股交易时间段
_TRADING_START = time(9, 15)
_TRADING_END = time(15, 10)


def is_trading_hours() -> bool:
    """判断当前是否在交易时间内 (09:15 ~ 15:10)。"""
    from datetime import datetime
    now = datetime.now().time()
    return _TRADING_START <= now <= _TRADING_END


def seconds_until_trading_start() -> float:
    """距离下一个交易时段开始的秒数。如果当前在交易时间内返回 0。"""
    from datetime import datetime, timedelta
    if is_trading_hours():
        return 0
    now = datetime.now()
    target = datetime.combine(now.date(), _TRADING_START)
    if now.time() > _TRADING_END:
        target = datetime.combine(now.date() + timedelta(days=1), _TRADING_START)
    delta = (target - now).total_seconds()
    return max(delta, 1)
