---
topic: 继续生成视频误开新脚本Plan
module: video-agent
date: 2026-08-08
keywords:
  - 继续生成视频
  - submit_turn
  - script_pipeline
  - prepare-scene-packages
  - queued
  - script_plan_confirmed
---
## 结论摘要
脚本 Skill 跑完后用户发「继续生成视频」时，旧逻辑因文案含「视频」再次种子 8 步脚本 Plan，并把短句写入 `latest_input`；若上轮 Turn 未结束还会排队。修复演进：前端先拦截成片意图；**必须先确认脚本方案**才启动资产包；后端仅在 `script_plan_confirmed` 时走路径 C，未确认则 inspect。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（发送拦截 + 确认门禁）
- `web/src/features/video-agent/scriptSkillStages.ts`
- `backend/pixelflow/video_agent/entrypoint.py`

## 核心逻辑
1. `isContinueVideoGenerationRequest` 收窄（不含裸「生成视频」）
2. 未确认 → 提示确认，不 `startVideoAgentAssetPackageFromScript`
3. 已确认 → 直接资产包；后端 C：`inspect_video_workspace` 且不覆盖 `latest_input`

## 注意事项
- 无脚本时提示先完成/保存脚本
- 角色设定不清时不进资产包，改走全流程 Plan（见 `script-plan-confirm-before-assets.md`）
- 资产包仍走 legacy `prepare-scene-packages` job
