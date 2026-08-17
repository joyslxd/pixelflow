---
topic: 确认脚本后由命令 API 启动资产包
module: video-agent
date: 2026-08-13
keywords:
  - confirmScriptPlanAndGenerateAssetPackage
  - confirm-script-plan
  - prepare_scene_packages
  - video_scene_packages
  - AgentPipelineProgress
  - refreshSnapshot
---

## 结论摘要

V2 脚本确认只做：命令 API 写 `script_plan_confirmed` 并启动 prepare → 开资产包进度卡 → `refreshSnapshot`。  
禁止再提交自然语言 Turn「确认脚本」；禁止内部回执脏文案。

`prepare_scene_packages` 完成后必须 `refreshSnapshot`，再投影旧工作流同款 `video_scene_packages` 卡片。公开摘要禁止「请前端展示」口吻。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`confirmVideoAgentScriptPlanWithRevisionRetry`）
- `backend/pixelflow/agent_runtime/service.py`（`confirm_video_agent_script_plan`）
- `backend/pixelflow/video_agent/tools/scene_packages.py`

## 核心逻辑

1. 确认 → `POST .../commands/confirm-script-plan`（可带 markdown）
2. 进度卡本地 `createAssetPackageProgressSteps`
3. Snapshot `scenePackageJob` / 场景包投影推进后续步骤

## 注意事项

- 详见 `confirm-script-plan-command-api.md`
- 用户手打「确认脚本」走 Agent + Tool 前置，不再 bootstrap prepare
