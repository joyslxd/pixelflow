---
topic: 去掉对话分镜视频预览卡 + 单镜并发生成
module: video-agent
date: 2026-08-15
keywords:
  - video_result
  - MessageBubble
  - upsertNativeSceneVideoPreviewFromWorkspace
  - beginArtifactAction
  - generate_scene
  - scene_video_progress
  - 确认并生成分镜
---

## 结论摘要

1. 对话区不再展示 early「分镜视频」预览大卡：`upsertNativeSceneVideoPreviewFromWorkspace` 只回填场景包消息的 `generatedSceneVideos`；`MessageBubble` 仅渲染带 `mergedVideo` 的 `video_result`，其余 `video_result` 显式 `null`，避免落到通用 artifact 按钮。
2. 单镜并发生成：`beginArtifactAction(..., \`generate_scene:${sceneId}\`)` 按镜互斥；V2 单镜不占会话 `busy`，分镜 5 生成中仍可点分镜 6/7。当前选中镜若已在 `generatingSceneIds` 则按钮禁用。
3. 进度：`resolveNativeSceneVideoBatchTotal` 对 `progress.total` 与 `jobTotal` 取 max；后端 `generate_scenes` 启动时按 workspace 全部 `generation_jobs` 写 `scene_video_progress`，避免后启单镜把 total 盖成 1。

## 相关文件

- `web/src/components/chat/MessageBubble.tsx`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/sceneVideoBatchTotal.ts`
- `backend/pixelflow/video_agent/tools/scene.py`

## 注意事项

- 同镜重复点击仍被 per-scene key 挡住；镜完成后若需再点同一镜，刷新或等 key 策略后续放开。
- 多 Turn 由 Runtime 排队，Operation 轮询可并行；进度板应看汇总 jobs，不要只信最近一批 progress。
- 合并成片卡（`mergedVideo`）与剪映三按钮仍走 `video_result` 分支，勿一并删掉。
