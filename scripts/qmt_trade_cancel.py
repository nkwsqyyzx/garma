#!/usr/bin/env python3
"""QMT 交易撤单"""
import argparse
import json
import sys

from qmt_base import api_post


def main():
    parser = argparse.ArgumentParser(description="QMT 交易撤单")
    parser.add_argument("order_id", help="委托编号")
    args = parser.parse_args()

    result = api_post("/trade/cancel", {"order_id": args.order_id})
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("code") != 0:
        print(f"\n撤单失败: {result.get('message', '未知错误')}")
        sys.exit(1)

    print("\n撤单成功")


if __name__ == "__main__":
    main()
