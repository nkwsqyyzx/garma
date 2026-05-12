#!/usr/bin/env python3
"""QMT 交易下单：买入/卖出股票"""
import argparse
import json
import sys
import time

from qmt_base import api_post, api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 交易下单")
    parser.add_argument("stock_code", help="股票代码，如 510310.SH")
    parser.add_argument("order_type", choices=["buy", "sell"], help="买卖方向")
    parser.add_argument("volume", type=int, help="数量（股）")
    parser.add_argument("price", type=float, help="价格")
    parser.add_argument("--price-type", default="limit", choices=["limit", "market"],
                        help="价格类型，默认 limit")
    parser.add_argument("--strategy", default="", help="策略名称")
    parser.add_argument("--remark", default="", help="备注")
    args = parser.parse_args()

    order_data = {
        "stock_code": args.stock_code,
        "order_type": args.order_type,
        "order_volume": args.volume,
        "price_type": args.price_type,
        "price": args.price,
    }
    if args.strategy:
        order_data["strategy_name"] = args.strategy
    if args.remark:
        order_data["order_remark"] = args.remark

    result = api_post("/trade/order", order_data)
    print("下单结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("code") != 0:
        print(f"\n下单失败: {result.get('message', '未知错误')}")
        sys.exit(1)

    req_id = result.get("data", {}).get("req_id", "")
    if req_id:
        print(f"\n委托号: {req_id}")

    # 等待一下再查成交
    print("\n等待 3 秒后查询当日成交...")
    time.sleep(3)

    trades_data = api_get("/account/trades")
    if trades_data.get("code") == 0:
        trades = trades_data.get("data", [])
        if trades:
            from collections import defaultdict
            from datetime import datetime as dt

            print(f"\n当日成交共 {len(trades)} 笔\n")

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
                ts = dt.fromtimestamp(t["traded_time"]).strftime("%H:%M:%S")
                direction = "买入" if t["order_type"] == "buy" else "卖出"
                print(f"{ts:<12} {t['stock_code']:<14} {direction:<6} {t['traded_price']:>8.3f} {t['traded_volume']:>6} {t['traded_amount']:>10.2f}")
        else:
            print("暂无成交记录")


if __name__ == "__main__":
    main()
