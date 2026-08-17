---
topic: FORCED STOP 重复调工具与确认单 tuple 入库失败
module: video-agent
date: 2026-08-17
keywords:
  - FORCED STOP
  - LoopDetectionMiddleware
  - native_pending_confirmation
  - scene_ids tuple
  - compose_or_export_video
---

## 结论摘要

`[FORCED STOP] Repeated tool calls exceeded the safety limit` 来自 DeerFlow
`LoopDetectionMiddleware`：同一轮里相同 tool call 反复出现（默认约 5 次）后强制停。
不是 merge 供应商接口报错。今早会话里触发链是：模型反复调计费工具 → 闸门要写
`native_pending_confirmation` → `arguments.scene_ids` 是 tuple 无法进 JSON → persist 失败
→ 模型继续重试 → 撞 hard limit。

## 关键文件

- `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py`
- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/tests/test_video_agent_tool_gateway_gates.py`

## 核心逻辑

1. warn ≈ 3 次相同 call hash；hard ≈ 5 次强制停并注入 FORCED STOP 文案。
2. pending.arguments 入库前必须 `_json_safe_value`（tuple→list）。

## 注意事项

- 看到 FORCED STOP 先查 gateway 是否 `persist native_pending_confirmation failed`。
- 合并未真正 start_delivery 时 deliveries 仍为空。
