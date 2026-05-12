#!/usr/bin/env python3
"""QMT 查询当日委托"""
import argparse
import sys

from qmt_base import api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 查询当日委托")
    parser.add_argument("--cancelable", action="store_true", help="仅返回可撤委托")
    args = parser.parse_args()

    params = {}
    if args.cancelable:
        params["cancelable_only"] = "true"

    data = api_get("/account/orders", params=params if params else None)
    if data.get("code") != 0:
        print(f"查询失败: {data.get('message', '未知错误')}")
        sys.exit(1)

    orders = data.get("data", [])
    if not orders:
        print("当日无委托记录")
        return

    print(f"当日委托共 {len(orders)} 笔\n")
    print(f"{'委托编号':<16} {'股票代码':<14} {'方向':<6} {'价格':>8} {'数量':>8} {'成交':>8} {'状态':<10}")
    print("-" * 78)
    for o in orders:
        oid = str(o.get("order_id", ""))
        code = o.get("stock_code", "")
        direction = "买入" if o.get("order_type") == "buy" else "卖出"
        price = float(o.get("price", 0))
        vol = o.get("order_volume", 0)
        traded = o.get("traded_volume", 0)
        status = o.get("order_status", "")
        print(f"{oid:<16} {code:<14} {direction:<6} {price:>8.3f} {vol:>8} {traded:>8} {status:<10}")


if __name__ == "__main__":
    main()
