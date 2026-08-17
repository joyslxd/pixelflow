---
topic: 重生分镜视频未渲染在视频场景包
module: video-agent
date: 2026-08-17
keywords:
  - generatedSceneVideos
  - video_scene_packages
  - previewAssets
  - upsertNativeSceneVideoPreviewFromWorkspace
  - 查看分镜
  - 单镜重生
---

## 结论摘要

去掉对话区 early「分镜视频」预览卡后，成片只应回填到「视频场景包」。但：

1. Agent tip 仍写「会出现预览卡 / 场景包不是成片入口」——与实现相反，用户找不到预览。
2. 场景包卡顶栏 `previewAssets` 只取全局参考图，即使消息已有 `generatedSceneVideos` 也不播成片。
3. 单镜重生时若 Workspace 短暂缺 URL，upsert 用空 `scene_videos` 覆盖，会把卡上已有成片清掉。

修复：改 tip；顶栏优先渲染分镜视频；upsert / 打开面板按 `scene_id` 合并保留旧成片。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑

1. `generate_scenes` 启动回复：引导「视频场景包 → 查看分镜」
2. `previewAssets`：有 `generatedSceneVideos.video_url` 时用 `<video>` 顶栏预览
3. 回填合并：消息已有 ∪ Workspace 新 URL（同 id 以 Workspace 为准）

## 注意事项

- 合并成品仍走带 `mergedVideo` 的 `video_result`，勿与分镜片段混卡
- 无 URL 时顶栏仍显示参考图；打开分镜面再靠 Workspace merge
