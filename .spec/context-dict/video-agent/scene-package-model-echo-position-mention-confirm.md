---
topic: 选模回显、场景包卡位置、分镜@与确认成片
module: video-agent
date: 2026-08-13
keywords:
  - scene_asset_model_options
  - confirmedModel
  - seeddream-5.0
  - insertBeforeId
  - SceneMentionEditor
  - 确认并生成视频
  - sceneAssetsAwaitingModel
---

## 结论摘要

确认 Seedream 后选模卡仍高亮 image-2：MessageBubble 在 `sceneAssetModelConfirmed` 时仍默认 `preferred=gpt-image-2`，未读 `creation_contract.image_model`。

场景包卡重投错位：只认稳定 id、缺失时 append，易落到末尾或另开一张；应优先原地更新时间线最早的 `video_scene_packages`，新建时 `insertBeforeId` 插到选模卡前，并去重。

投影曾用 `sceneAssetsAwaitingModel=!hasImages`，选模后仍 awaiting，藏掉「确认并生成视频」。应按选模/生图态分流；有图后 awaiting/generating 皆 false。

分镜 @：结构化镜头描述默认收起 `SceneMentionEditor`，现改为始终展示可 @ 编辑器。

## 关键文件

- `web/src/components/chat/MessageBubble.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/lib/conversationRouting.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`

## 核心逻辑

1. 选模回显：`confirmedModel` ← contract.image_model；已确认时 `selected=confirmedModel`
2. 投影：`targetMessageId=最早场景包卡||stableId`；新建 `insertBefore` 选模卡；同会话去重
3. flags：`generating=!hasImages&&(running||modelConfirmed)`；`awaiting=!hasImages&&!generating&&!modelConfirmed`
4. Storyboard 始终渲染 `SceneMentionEditor`

## 注意事项

- 确认成片仍走 Turn → `generate_scenes`，不恢复旧 Job HTTP
- 无参考图时「确认并生成视频」仍隐藏，属预期
