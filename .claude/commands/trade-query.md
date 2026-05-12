---
description: "QMT查询委托状态"
arguments:
  - name: "args"
    description: "格式: <req_id>"
    required: true
---

请执行以下命令查询 QMT 委托状态：

**参数解析（从 $ARGUMENTS 中提取）：**
- `req_id`（请求 ID）

```bash
python3 scripts/qmt_trade_query.py <req_id>
```

将参数替换为实际值后执行，展示输出结果。如果失败，说明错误原因。
