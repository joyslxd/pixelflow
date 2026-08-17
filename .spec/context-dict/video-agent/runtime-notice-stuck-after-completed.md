---
topic: input.completed 误映射 accepted 导致假处理中
module: video-agent
date: 2026-08-13
keywords:
  - 正在处理中
  - runtimeNotice
  - input.state_changed
  - completed
  - accepted
  - inputQueue
---

## 结论摘要

原生 Agent 已返回气泡且后端 Turn 已 `completed`，前端仍显示「正在处理中，请稍候…」。

根因：SSE `input.state_changed(status=completed)` 被 reducer 映射成 `accepted`；
`resolveSupervisorRuntimeNotice` 把 `accepted` 视为活跃占用（VideoAgent 整轮常停在 ACCEPTED）。
完成事件等于「永远还在处理」。Snapshot 本会排除 COMPLETED Turn，但 live SSE 路径从未对齐。

## 关键文件

- `web/src/lib/supervisor/reducer.ts`（`applyInputEvent`）
- `web/src/lib/supervisor/runtimeNotice.ts`
- `backend/pixelflow/agent_runtime/service.py`（Snapshot `input_queue` 排除 COMPLETED）

## 核心逻辑

1. wire `completed` → 从 `inputQueue` 移除该项（与 Snapshot 一致）。
2. 若队列无 sending/queued/processing/accepted，将 `run` 置 `idle`。
3. `mapInputStatus` 不再把 `completed` 映射为 `accepted`。

## 注意事项

- VideoAgent 执行中 Turn 常为 `accepted`（不一定有 `processing` 事件），notice 仍应对 accepted 显示处理中。
- 刷新 Snapshot 也能清，但 live 路径必须本地消化 completed。
