---
description: "QMT查询当日委托"
arguments:
  - name: "args"
    description: "可选: cancelable（仅返回可撤委托）"
    required: false
---

请执行以下命令查询 QMT 当日委托：

**参数解析（从 $ARGUMENTS 中提取）：**
- 如果参数包含 `cancelable` 或 `可撤`，添加 `--cancelable` 参数

```bash
python3 scripts/qmt_account_orders.py [--cancelable]
```

展示输出结果。如果失败，说明错误原因。
