---
topic: 场景视频增量进度与分镜预览卡
module: video-agent
date: 2026-08-10
keywords:
  - video_progress
  - generate-scenes
  - sceneVideosGenerating
  - upsertEarlySceneVideoCard
  - 分镜预览
---
## 结论摘要
`generate-scenes` Job 在每镜完成后回写 `video_progress` + 部分 `result.scene_videos`。前端轮询时 upsert `media-result:scene_videos:{job_id}` 预览卡（`sceneVideosGenerating=true`），固定 tip 展示「分镜视频 x/y」，已完成片段可立即播放；整批结束后清 generating 再进 merge。

## 关键文件
- `backend/app/gateway/routers/pixelflow_video.py`（`SceneVideoGenerationProgress`、`on_progress`）
- `web/src/lib/api.ts`（`video_progress`、`pollSceneVideoJob` onProgress）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`upsertEarlySceneVideoCard` / `syncSceneVideoProgress`）
- `web/src/components/chat/MessageBubble.tsx`（生成中徽章与预览区）
- `web/src/lib/chat.ts`（`sceneVideosGenerating`）

## 核心逻辑
1. 并行 `run_scene` 结束后在锁内累计并 `on_progress`
2. Job status running 时即可带部分 `result`；message 含「分镜视频进度」
3. FE：early card 与终态卡共用 media-result id；完成时若仍 generating 则 upsert 清标志
4. 重生成 / 失败重试用 `mergePartialGeneratedSceneVideos` 保留未受影响镜

## 注意事项
- 厂商不提供帧级流式；增量预览 = URL 就绪即展示
- tip id 固定 `scene-video-progress-tip:{job_id}`，避免刷屏
- 同步 `POST /generate-scenes` 仍无进度（仅 Job 路径）
