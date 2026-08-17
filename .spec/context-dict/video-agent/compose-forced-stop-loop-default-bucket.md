---
topic: FORCED STOP 合并循环因 LoopDetection 全局 default 桶与确认后盲调
module: video-agent
date: 2026-08-17
keywords:
  - FORCED STOP
  - LoopDetectionMiddleware
  - thread_id
  - compose_or_export_video
  - requires_confirmation
  - 重新发起合并
---

## 结论摘要

`[FORCED STOP] Repeated tool calls exceeded the safety limit` **不是** content-app
合并接口坏了。根因有两层：

1. `native_invoke` 虽为每 Turn 生成独立 LangGraph `configurable.thread_id`，但未写入
   `runtime.context.thread_id`；`LoopDetectionMiddleware` 读不到后全部落入进程内
   **`default` 桶**。多次「合并视频吧 / 重新发起合并 / 点确认 resume」累计相同
   `compose_or_export_video({output_type:mp4})` 后，后续 Turn 会**秒级**硬停。
2. 确认闸门返回 `requires_confirmation` 后 ReAct 仍可同轮连打同一 Tool；
   `VideoToolCommitmentMiddleware` 还会把口述「合并」再次强制成 tool_call。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`（注入 `thread_id`/`run_id`）
- `backend/pixelflow/video_agent/tool_gateway.py`（确认结果附带停手提示）
- `backend/pixelflow/video_agent/middleware/tool_gateway.py`
  （`VideoConfirmationAwaitMiddleware`，须排在 LoopDetection **之后**）
- `backend/pixelflow/video_agent/middleware/tool_commitment.py`
- `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py`

## 核心逻辑

1. after_model 按中间件注册顺序**反向**执行：确认剥离必须注册在 LoopDetection 之后，
   才能先剥 tool_calls 再计数。
2. 热修后仍看到秒级 FORCED STOP：先重启 gateway 清掉内存里的 `default` 桶残留。

## 注意事项

- 合并仍走 ReAct + 确认闸门，不做确定性 bootstrap。
- UI「重新发起合并」只是发「合并视频吧」；修好后应先出可点确认卡，再真正 merge。
