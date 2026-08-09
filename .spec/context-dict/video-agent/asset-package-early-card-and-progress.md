---
topic: 资产包第二步提前出卡与参考图进度
module: video-agent
date: 2026-08-09
keywords:
  - asset_progress
  - sceneAssetsGenerating
  - AgentPipelineProgress
  - generate_scene_assets
  - prepare-scene-packages
---
## 结论摘要
`prepare-scene-packages` 进入 `generate_scene_assets` 后，Job 已带场景包结构；前端应立刻 upsert 可点开的视频资产包卡（`sceneAssetsGenerating=true`，禁用确认成片）。参考图逐张完成后后端回写 `asset_progress`，前端更新执行规划步骤详情、耗时，并用固定 tip 消息展示「参考图 x/y」。

## 关键文件
- `backend/pixelflow/generate/scene_assets.py`（`on_progress`）
- `backend/app/gateway/routers/pixelflow_video.py`（`SceneAssetGenerationProgress`）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/MessageBubble.tsx`

## 核心逻辑
1. 每张图结束后 `on_progress` → Job.`asset_progress` + 部分 `videoScenePackages`
2. FE 轮询：step2 完成即出卡；progress tick 更新 tip/卡片预览
3. 步骤 `startedAt/completedAt/durationMs` 展示耗时；running 秒级刷新

## 注意事项
- tip 消息 id 固定为 `scene-package-asset-tip:{job_id}`，避免刷屏
- 资产包卡与终态卡共用 `scenePackageJobMessageId`，终态清除 `sceneAssetsGenerating`
