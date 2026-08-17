---
topic: 确认脚本 PUT 409 需带回 current_revision 才能重试
module: video-agent
date: 2026-08-13
keywords:
  - PUT video-agent/script
  - 409
  - confirm_for_generation
  - current_revision
  - saveVideoAgentScriptWithRevisionRetry
  - applyVideoWorkspaceSnapshot
---

## 结论摘要

确认脚本时 `PUT …/video-agent/script`（`confirm_for_generation=true`）若带过期 `expected_revision`，会 409。旧链路只靠刷新 Snapshot 取新 revision；当投影滞后、同 revision CAS 竞态、或同会话换了 `workspace_id` 时，前端会判定「revision 没变」直接放弃。更糟的是确认保存失败曾被静默吞掉，随后仍 `turns/start`，`script_plan_confirmed` 可能从未落地。

修复：409 `detail` 带 `current_revision`/`workspace_id`；FE 优先用该值重试，同 revision CAS 再短暂重试；`workspaceId` 变化时 Snapshot 必须替换；确认保存失败要抛出，不再吞掉。

## 相关文件

- `backend/pixelflow/agent_runtime/service.py`（`AgentRuntimeVideoScriptConflictError`）
- `backend/app/gateway/routers/pixelflow_conversations.py`
- `web/src/lib/supervisor/api.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`

## 核心逻辑

1. revision 不匹配或 `apply_workspace_patch` CAS 冲突 → 409 + 权威 `current_revision`
2. `SupervisorApiError` 仅在 HTTP 409 解析结构化 detail（其它错误仍不读正文）
3. `saveVideoAgentScriptWithRevisionRetry`：`conflictRevision ?? snapshotRevision`；同值则短暂后再试
4. `applyVideoWorkspaceSnapshot`：`workspaceId` 不同则强制采用新 Snapshot

## 注意事项

- 用户可见症状：Network 里一次 409 + Snapshot 200，但没有第二次 PUT script，却有 `turns/start`
- 已污染会话需重新点「确认脚本」；确认失败应出现助手错误文案而不是假进度
- 勿对非 409 错误正文做通用信任解析
- `commands/confirm-script-plan` 同理：首跳 409 + `current_revision: N` 后应有第二次 POST 200；仅看红字 409 会误判失败。确认前应先 `refreshSnapshot` 对齐 revision。
