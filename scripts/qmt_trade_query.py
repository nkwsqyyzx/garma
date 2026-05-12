#!/usr/bin/env python3
"""QMT 查询委托状态"""
import argparse
import json
import sys

from qmt_base import api_get


def main():
    parser = argparse.ArgumentParser(description="QMT 查询委托状态")
    parser.add_argument("req_id", help="请求 ID (req_id)")
    args = parser.parse_args()

    data = api_get(f"/trade/order/{args.req_id}")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    if data.get("code") != 0:
        print(f"\n查询失败: {data.get('message', '未知错误')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
