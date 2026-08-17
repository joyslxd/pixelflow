---
topic: 合并确认 409（pending revision 自漂移）
module: video-agent
date: 2026-08-16
keywords:
  - compose_or_export_video
  - native_pending_confirmation
  - expected_revision
  - 409
  - video_agent_confirmation_conflict
---

## 结论摘要

点击「确认执行」合并成片时 `POST …/video-agent/confirmations/…/responses` 恒 409，前端显示「确认请求未完成，请刷新后重试」。根因是 Gateway 持久化 `native_pending_confirmation` 会 bump workspace revision，但 pending 内 `expected_revision` 仍是写入前的值；确认 API 要求二者相等，因此永远 Conflict。另：确认成功后 resume 未把 Authorization credential 交给 Runner，合并即便过闸也会缺凭据。

## 关键文件

- `backend/pixelflow/video_agent/tool_gateway.py`（`_persist_pending_confirmation`）
- `backend/pixelflow/agent_runtime/service.py`（`_respond_to_native_confirmation`）
- `backend/tests/test_video_agent_tool_gateway_gates.py`
- `backend/tests/test_video_agent_confirmation_api.py`

## 核心逻辑

1. 闸门生成 pending 时 `expected_revision = workspace.revision`（写入前）。
2. `apply_workspace_patch` 写入 pending 后 revision +1。
3. 确认校验：`pending.expected_revision != workspace.revision` → 409。
4. 修复：persist 时一次性写入 `expected_revision = workspace.revision + 1`；确认侧兼容旧 pending 的 `revision - 1`；resume 时 hand-off credential。

## 注意事项

- 已卡住的对话可直接再点「确认执行」（兼容 -1）；新发起的确认 pending 已对齐。
- 勿再引入「合并绕过确认/确定性 bootstrap」；合并仍走 ReAct + 确认闸门。
