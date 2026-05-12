---
description: "QMT交易撤单"
arguments:
  - name: "args"
    description: "格式: <order_id>"
    required: true
---

请执行以下命令进行 QMT 交易撤单：

**参数解析（从 $ARGUMENTS 中提取）：**
- `order_id`（委托编号）

```bash
python3 scripts/qmt_trade_cancel.py <order_id>
```

将参数替换为实际值后执行，展示输出结果。如果失败，说明错误原因。
