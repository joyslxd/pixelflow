---
topic: 参考图结果卡与 early 待生成卡顺序错乱
module: video-agent
date: 2026-08-10
keywords:
  - sceneAssetProgressArchived
  - media-result
  - 待生成
  - start_images
  - sessionStorage last-scene-asset-model
---

## 结论摘要

一轮生成完成后会出现「带图结果卡」+ 多张「待生成」进度卡叠在一起，看起来像顺序乱了。根因是 early 进度卡与 media-result 结果卡并存，且「开始生图」会用 sessionStorage 旧模型自动再开一轮，把新的空进度卡插到结果卡之后。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/lib/chat.ts`（`sceneAssetProgressArchived`）

## 核心逻辑

1. Job 完成：归档当前/陈旧空进度卡（`sceneAssetProgressArchived=true`），结果只看 `media-result:scene_assets:{job_id}`。
2. NL「开始生图」：若会话已有带图场景包，提示去结果卡，禁止用 prior model 自动重跑。
3. 恢复时优先选有图场景包卡，不被无图 early 卡带偏。

## 注意事项

- 同一次生成中进度卡仍会原地更新；完成后应变归档文案，不再显示「待生成」五格。
- 真要重跑只能从结果卡「重新生成参考图」，不要依赖「开始生图」+ sessionStorage。
