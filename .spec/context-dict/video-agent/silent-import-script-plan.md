---
topic: 静默导入成熟脚本（已取消）
module: video-agent
date: 2026-08-11
keywords:
  - import_script
  - 导入成熟脚本
  - AgentPlanTimeline
  - isSilentImportScriptPlan
  - agent.plan.updated
---

## 结论摘要

V2 已取消 `isSilentImportScriptPlan` / `isSilentProductionFieldsPlan` 隐藏逻辑：**所有 Plan 都在对话流展示**。导入 / 补字段计划同样渲染 `AgentPlanTimeline`。

入场思考改为后台并行：先发 `agent.plan.created`（`public_goal=规划中`，无步骤），Planner 落库后再发 `agent.plan.updated` 带步骤清单；思考流不阻塞 Planner。

## 相关文件

- `web/src/features/video-agent/scriptSkillStages.ts`（静默判定恒为 false）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（不再过滤时间线）
- `backend/pixelflow/video_agent/entrypoint.py`（并行思考 + scaffold/update）
- `backend/pixelflow/video_agent/executor/events.py`（`build_plan_updated_event`）
- `web/tests/silentImportScriptPlan.test.mjs`

## 核心逻辑

1. scaffold：仅 SSE `plan.created`，不落库空 Plan（`save_plan` 仍是 create-once）
2. Planner 完成后 `save_plan` + `plan.updated`（steps 列表）
3. FE reducer 合并 steps；思考流不因 plan/step 事件强制 completed

## 注意事项

- 勿再把导入/补字段结果改写为仅对话框 notice 而藏卡
- Executor 思考与入场思考同 turn：submit_turn 在返回前仍 await 入场思考，避免竞态 complete
