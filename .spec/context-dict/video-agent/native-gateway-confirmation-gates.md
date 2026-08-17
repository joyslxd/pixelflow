---
topic: Native Gateway 确认额度 revision 闸门
module: video-agent
date: 2026-08-12
keywords:
  - VideoToolGateway
  - native_pending_confirmation
  - native_approved_confirmation
  - NativeOperationResumeHandler
  - confirmation_required
---

## 结论摘要

P0-3 Gateway 在 `VideoToolGateway.invoke` 强制裁决：需确认 Tool 未批准则持久化
`native_pending_confirmation`、发 `agent.confirmation.requested` 并返回
`requires_confirmation=true`（不执行业务 Tool）。额度 `quota_interrupt` 存在时拦截
BILLABLE。破坏性/计费在 approved revision 与当前不一致时要求重确认。

确认 API 识别 native pending 后写入 `native_approved_confirmation` 并调度 Runner
内部 Turn；`native_invoke` 把 approved 注入 runtime context。

Operation 成功经 `NativeOperationResumeHandler`：收口 RUNNING 步骤、claim 幂等、
内部 invoke Agent，不再走 `executor.resume_plan`。

## 关键文件

- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/pixelflow/video_agent/confirmation.py`
- `backend/pixelflow/video_agent/middleware/tool_gateway.py`
- `backend/pixelflow/video_agent/native_operation_resume.py`
- `backend/pixelflow/agent_runtime/service.py`（`_respond_to_native_confirmation`）
- `web/src/features/native-video-agent/cards/index.tsx`

## 核心逻辑

1. 确认身份：`native_confirmation_id(plan_id, tool_call_id)`。
2. 放行条件：approved.tool_name/call_id 匹配且 revision 一致。
3. Operation resume claim：`workspace.payload.native_operation_resume_claimed.event_id`。

## 注意事项

- 旧 Plan/Step 确认链路仍保留；native pending 优先匹配 confirmation_id。
- Tool 执行成功后清掉 pending/approved，避免下一 Turn 误放行。
- FE 新卡与 Legacy `AgentConfirmationCard` 并存；P0-5 前可双轨展示。
