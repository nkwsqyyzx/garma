#!/usr/bin/env python3
"""QMT 查询账户资金"""
import argparse
import sys

from qmt_base import api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 查询账户资金")
    parser.parse_args()

    data = api_get("/account/asset")
    if data.get("code") != 0:
        print(f"查询失败: {data.get('message', '未知错误')}")
        sys.exit(1)

    asset = data.get("data", {})
    if not asset:
        print("无资金数据")
        return

    fields = [
        ("总资产", "total_asset"),
        ("可用资金", "cash"),
        ("冻结资金", "frozen_cash"),
        ("持仓市值", "market_value"),
        ("账户余额", "balance"),
    ]
    print(f"{'项目':<12} {'金额':>14}")
    print("-" * 30)
    for label, key in fields:
        val = asset.get(key)
        if val is not None:
            print(f"{label:<12} {float(val):>14.2f}")


if __name__ == "__main__":
    main()
