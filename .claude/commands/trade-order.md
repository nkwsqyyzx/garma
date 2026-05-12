---
description: "QMT交易下单：买入/卖出股票"
arguments:
  - name: "args"
    description: "格式: <stock_code> <buy|sell> <volume> <price> [limit|market] [strategy_name] [order_remark]"
    required: true
---

请执行以下命令进行 QMT 交易下单：

**参数解析（从 $ARGUMENTS 中提取）：**
- `stock_code`（股票代码，如 510310.SH）
- `order_type`（buy 或 sell）
- `volume`（数量，股）
- `price`（价格）
- `price_type`（可选，默认 limit；market 表示市价）
- `strategy_name`（可选，策略名称，通过 --strategy 传入）
- `order_remark`（可选，备注，通过 --remark 传入）

```bash
python3 scripts/qmt_trade_order.py <stock_code> <buy|sell> <volume> <price> [--price-type limit|market] [--strategy 名称] [--remark 备注]
```

将参数替换为实际值后执行，展示输出结果。如果失败，说明错误原因。
