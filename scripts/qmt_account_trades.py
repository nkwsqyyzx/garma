#!/usr/bin/env python3
"""QMT 查询当日成交"""
import argparse
import sys
from collections import defaultdict
from datetime import datetime

from qmt_base import api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 查询当日成交")
    parser.parse_args()

    data = api_get("/account/trades")
    if data.get("code") != 0:
        print(f"查询失败: {data.get('message', '未知错误')}")
        sys.exit(1)

    trades = data.get("data", [])
    if not trades:
        print("当日无成交记录")
        return

    print(f"当日成交共 {len(trades)} 笔\n")

    # 汇总
    stocks = defaultdict(lambda: {"buy_vol": 0, "sell_vol": 0, "buy_amt": 0.0, "sell_amt": 0.0})
    for t in trades:
        s = stocks[t["stock_code"]]
        if t["order_type"] == "buy":
            s["buy_vol"] += t["traded_volume"]
            s["buy_amt"] += t["traded_amount"]
        else:
            s["sell_vol"] += t["traded_volume"]
            s["sell_amt"] += t["traded_amount"]

    print(f"{'股票代码':<14} {'买入数量':>8} {'买入金额':>12} {'卖出数量':>8} {'卖出金额':>12}")
    print("-" * 60)
    for code in sorted(stocks.keys()):
        s = stocks[code]
        print(f"{code:<14} {s['buy_vol']:>8} {s['buy_amt']:>12.2f} {s['sell_vol']:>8} {s['sell_amt']:>12.2f}")

    print(f"\n--- 最近 20 笔成交明细 ---")
    print(f"{'成交时间':<12} {'股票代码':<14} {'方向':<6} {'价格':>8} {'数量':>6} {'金额':>10}")
    print("-" * 62)
    for t in trades[-20:]:
        ts = datetime.fromtimestamp(t["traded_time"]).strftime("%H:%M:%S")
        direction = "买入" if t["order_type"] == "buy" else "卖出"
        print(f"{ts:<12} {t['stock_code']:<14} {direction:<6} {t['traded_price']:>8.3f} {t['traded_volume']:>6} {t['traded_amount']:>10.2f}")


if __name__ == "__main__":
    main()
