---
topic: 409 后刷新对话错序与资产包进度卡消失
module: video-agent
date: 2026-08-13
keywords:
  - 409
  - afterMessageId
  - thinkingHistory
  - AgentPipelineProgress
  - scriptPlanConfirmed
  - scenePackageJob
  - 硬刷新
---

## 结论摘要

用户看到 `VideoAgent conflict / 请刷新后重试` 后硬刷新，常伴随：① Thought/Turn 全挤到最新用户消息后（顺序错乱）；②「执行规划 · 视频资产包」进度卡消失。

根因：
1. 硬刷新丢失 `thinkingTurnAnchorsRef`；`resolveThinkingAfterMessageId` 无锚点时回落**最近用户消息**；Snapshot `thinkingHistory` 原先不带 `afterMessageId`。
2. `assetPackageProgressSteps` 只在 React 内存；native tool 事件不进 thinkingHistory，刷新后无法驱动进度卡。

## 相关文件

- `backend/pixelflow/agent_runtime/service.py`（thinkingHistory 附 `clientInputId`/`afterMessageId`）
- `web/src/features/video-agent/thinkingAnchor.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`（`scriptPlanConfirmed` / `scenePackageJob`）

## 核心逻辑

1. Snapshot 折叠思考时按 Turn 表补 `after_message_id = client_input_id`（与 FE 消息 id 一致）。
2. 锚点解析优先 `afterMessageId`，再 `knownAnchor` / pending，最后才回落最新用户消息。
3. 刷新后用 Workspace：`scene_package_job` 活跃或已确认脚本 → prepare；已有 packages 无图 → awaiting_image_model；有图 → completed。

## 注意事项

- 409 本身可能是 workspace revision / 双 Workspace / context version；本条修的是**刷新后可恢复**，不是消灭全部 409。
- live 进行中/失败进度不被 snapshot 回退覆盖。
- tool 活动卡片硬刷新后仍可能缺（事件未 fold 进 history）；Thought/回答与进度卡应可恢复。
