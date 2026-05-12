#!/usr/bin/env python3
"""QMT 查询持仓"""
import argparse
import sys

from qmt_base import api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 查询持仓")
    parser.parse_args()

    data = api_get("/account/positions")
    if data.get("code") != 0:
        print(f"查询失败: {data.get('message', '未知错误')}")
        sys.exit(1)

    positions = data.get("data", [])
    if not positions:
        print("当前无持仓")
        return

    print(f"{'股票代码':<14} {'名称':<10} {'数量':>8} {'可用':>8} {'成本价':>10} {'市价':>10} {'市值':>12} {'盈亏':>10}")
    print("-" * 90)
    for p in positions:
        code = p.get("stock_code", "")
        name = p.get("stock_name", "")
        vol = p.get("volume", 0)
        can_use = p.get("can_use_volume", 0)
        cost = float(p.get("open_price", 0))
        market_price = float(p.get("market_price", 0))
        market_val = float(p.get("market_value", 0))
        profit = float(p.get("profit", 0))
        print(f"{code:<14} {name:<10} {vol:>8} {can_use:>8} {cost:>10.3f} {market_price:>10.3f} {market_val:>12.2f} {profit:>10.2f}")


if __name__ == "__main__":
    main()
