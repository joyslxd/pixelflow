---
topic: 资产包待确认阶段支持自然语言重做与修改
module: video-agent
date: 2026-08-08
keywords:
  - 重新生成视频资产包
  - isRegenerateVideoAssetPackageRequest
  - isReviseVideoAssetPackageRequest
  - revisionFeedback
  - video_scene_packages
  - 待确认
---
## 结论摘要
视频资产包进入「待确认」后，除卡片「确认并生成视频」外，用户可用自然语言重做或修改资产包。发送「重新生成视频资产包」或「把阿杰改成短发」等指令时，前端拦截并带修改意见重新跑 `prepare-scene-packages`，不再误入脚本确认门禁或空转。

## 关键文件
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`handleSend` 优先分支、`startVideoAgentAssetPackageFromScript`）

## 核心逻辑
1. 已有 `video_scene_packages` 且说「确认并生成视频」→ `handleGenerateVideoFromScenePackages`
2. 「重新生成*资产包/场景包」或待确认下的角色/道具修改短句 → 重跑资产包
3. 非纯重做时把用户原文写入 plan.md 的「用户修改意见」段，供 LLM/抽资产遵循

## 注意事项
- 资产包任务仍在跑时提示稍候，不并行开第二份
- 纯「重新生成视频资产包」不附加修改段；带「改成/调整」等才写入意见
- 首次尚未确认脚本时的「继续生成」仍走原确认门禁
