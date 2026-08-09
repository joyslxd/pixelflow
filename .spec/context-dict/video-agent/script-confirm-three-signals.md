---
topic: 三种确认信号统一进资产包
module: video-agent
date: 2026-08-09
keywords:
  - 同意方案
  - 确认脚本
  - confirmScriptPlanAndGenerateAssetPackage
  - scriptPlanConfirmForAssets
  - 核心产品
  - workflowTaskBoard
---
## 结论摘要
右下角「确认脚本并生成资产包」、对话框「同意方案」、自然语言「确认脚本/同意方案」三者等价，统一走 `confirmScriptPlanAndGenerateAssetPackage` → 回执「已确认脚本方案，正在生成视频资产包…」→ `prepare-scene-packages`。截图里「任务 7」来自 `workflowTaskBoard.ts` 固定阶段模板；Video Agent 有 Plan 时优先展示 Agent Plan 步骤。场景包失败「核心产品过于泛化」是默认/设定标题把泛化道具名带进 `global_assets`，生图前校验拒绝。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/lib/workflowTaskBoard.ts`
- `backend/pixelflow/generate/scene_packages.py`
- `backend/pixelflow/creative/asset_manifest.py`

## 核心逻辑
1. 脚本卡 `scriptPlanConfirmForAssets`；点「同意方案」不再走旧 plan.md `handleApprovePlan` 合同路径。
2. 需求大变时提示回复「重新设计任务规划」再重跑 Plan。
3. 道具标题「核心产品」从正文抽具体名；默认资产禁止写入「核心产品/目标用户」。

## 注意事项
- Agent Plan 仍是脚本 Skill 八阶段模板，不是按 brief 自由生成的任意 DAG；真正「按需求动态拆任务」还需 planner 升级。
- 旧会话里已带「核心产品」的资产包需重新确认生成。
