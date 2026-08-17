---
topic: 合并确认卡死后端无 pending 前端假等待
module: video-agent
date: 2026-08-16
keywords:
  - awaiting_confirmation
  - preferRicherPlan
  - native_pending_confirmation
  - workspaceProjection
  - 卡住无法发送
---

## 结论摘要

确认 resume 后闸门若再次要确认，但 `persist pending` 因 revision 冲突失败，则 DB 无
`native_pending`，Snapshot 的 confirmation 为 null；同时前端用 `preferRicherPlan`
按「步骤更多」保留 confirmation.requested 本地 upsert 的 awaiting 步骤，盖住服务端
`completed`，UI 永久「等待确认」，用户以为不能发「合并视频吧」。另：原生 confirmation
无 Plan waiting step 时，旧 Snapshot 校验会 `fail()`，刷新无法纠正。

## 关键文件

- `web/src/lib/supervisor/reducer.ts`（preferRicherPlan 终态优先）
- `web/src/lib/supervisor/workspaceProjection.ts`（允许无 waiting step 的 native confirmation）
- `backend/pixelflow/video_agent/tool_gateway.py`（gate 前重读 workspace；persist 冲突重试）

## 核心逻辑

1. Snapshot plan=`completed` + steps=[] 必须覆盖本地 awaiting upsert。
2. persist 失败不得只返回 requires_confirmation（否则口头确认、库无单）。
3. 执行前重读 `native_approved_confirmation`，避免 resume 快照过期。

## 注意事项

- 用户侧：硬刷新后再发「合并视频吧」；旧假确认卡应消失。
- 合并仍走 ReAct + 确认，不做确定性 bootstrap。
