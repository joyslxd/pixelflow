---
topic: 生成资产包 revision 冲突导致 RUNNING 僵尸与卡片错乱
module: video-agent
date: 2026-08-12
keywords:
  - prepare_scene_packages
  - AgentRuntimeRecordConflictError
  - workspace revision
  - apply_workspace_patch
  - videoAgentPlanAnchors
  - orphanActivityBlocks
---
## 结论摘要
`prepare_scene_packages` 跑完后写 `workspace_patch` 若仍用执行前的 `expected_revision`，并发 Turn 已 bump revision 时会抛 `VideoAgent workspace revision 已变化`。延迟提交整 Turn 失败，步骤留在 RUNNING（前端可显示 20+ 分钟），`scene_packages` / `scene_package_job` 未落库，后续无法查看资产包；completion_dispatch 也因未绑定 `plan_step_id` 反复 Conflict。同时旧锚点按 `planIndex→早期用户消息` 把「生成资产包」卡在对话中段，后续「检查工作区」反而更靠下。

## 关键文件
- `backend/pixelflow/video_agent/executor/service.py`
- `backend/pixelflow/video_agent/operation_resume.py`
- `backend/pixelflow/video_agent/entrypoint.py`（hydrate 无 job 时按 stage 回填）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/ChatPanel.tsx`

## 核心逻辑
1. Executor / QuotaResumer：`apply_workspace_patch` 冲突则重读 revision 最多 3 次；仍失败则 `fail_step`，禁止僵尸 RUNNING。
2. Hydrate：无 `scene_package_job` 时仍可从 `prepare_scene_packages:*` 完成事件回填包。
3. 进行中/最新 Plan 强制锚到最近用户轮次；同锚点下 RUNNING 排在已完成方案之后；orphan 挂最近用户消息。

## 注意事项
- 根因日志特征：`VideoAgent 延迟提交失败` + traceback 落在 `executor ... apply_workspace_patch` + `revision 已变化`。
- 用户当前卡死会话：刷新后发任意一句（或重试生成资产包）可触发 hydrate；若包已在完成事件中即可恢复查看。
