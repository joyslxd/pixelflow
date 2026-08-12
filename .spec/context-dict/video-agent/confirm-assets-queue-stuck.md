---
topic: 确认脚本后假排队与 Intake 误判 clarify
module: video-agent
date: 2026-08-12
keywords:
  - 确认脚本并生成资产包
  - merge_video_turn_content_with_history
  - CancelledError
  - QUEUED
  - prepare_scene_packages
  - script_plan_confirmed
---
## 结论摘要
「同意方案」后卡住有两层原因：1) `make dev --reload` 取消延迟提交时 Turn 仍占 ACCEPTED，下一条永远 QUEUED；2) 历史合并把「确认脚本并生成资产包」拼进整篇脚本，Intake 误判 `clarify/inspect`，长时间思考后仍不规划 `prepare_scene_packages`。修复：确认短令禁止历史拼接；脚本已确认且未出包时跳过长思考并给 Planner `continue_assets` 证据；延迟提交失败/取消必须收尾 Turn；无 Plan 的陈旧 ACCEPTED（≥180s）可释放。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/agent_runtime/service.py`
- `backend/pixelflow/video_agent/planner/model.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑
1. FE：`confirm_for_generation` 写 `script_plan_confirmed` → Turn「确认脚本并生成资产包」
2. BE：短令不拼历史；`_ready_to_prepare_scene_packages` → 跳过 Intake
3. Planner / 失败降级优先 `prepare_scene_packages`
4. `CancelledError`/`Exception` 后 `_complete_video_agent_runtime_turn`

## 注意事项
- 开发热重载会打断进行中 Turn；修后应自动释放，但仍建议确认时少触发 reload
- FE save 失败被静默吞掉时，确认短令仍可走准备闸门（不依赖 flag）
