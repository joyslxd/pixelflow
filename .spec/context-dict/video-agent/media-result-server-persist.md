---
topic: 生成图/视频结果由服务端幂等写入对话历史
module: video-agent
date: 2026-08-10
keywords:
  - media-result
  - conversation_id
  - scene_assets
  - durable history
  - context_json
---

## 结论摘要

供应商侧生成成功后，网关在 Job complete 路径立刻幂等追加 `media-result:{kind}:{job_id}` 结果卡，并 patch 会话 `context_json`。不再依赖前端 poll / `pushArtifact` 才能让历史可查。前端仍可用 early 进度卡；完成后清 `sceneAssetsGenerating`，若本地已有同 id 结果卡则跳过重复 push；恢复时优先有图结果卡 / context 快照。

## 关键文件

- `pixelflow/backend/pixelflow/generate/media_history.py`
- `pixelflow/backend/app/gateway/routers/pixelflow_video.py`
- `pixelflow/backend/app/gateway/routers/pixelflow_image.py`
- `pixelflow/web/src/lib/scenePackageAssetUi.ts`
- `pixelflow/web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑

1. Start body 可选 `conversation_id`，同时读 `X-Conversation-Id`；Job 元数据带上会话与 user。
2. `persist_media_result_message`：`client_message_id = media-result:{kind}:{job_id}`，与会话消息 uuid5 幂等键一致；二次 complete 不插第二条。
3. kind：`scene_assets` / `scene_videos` / `merge_video` / `image_generate` / `image_asset_edit` / `image_asset_fusion`；有 URL 的 success/partial 才落库。
4. FE：start 显式传 `conversation_id`；poll 完成去重；`preferredVideoScenePackagesMessageIndex` + `resolveVideoScenePackagesForRestore` 避免无图 early 卡挡住有图历史。

## 注意事项

- Job 仍是进程内存；网关重启会使未完成 Job 404，但已落库的结果卡与 context 仍在。
- 无 conversation_id 时仍可生成（dev 直调），但会 warning 且不保证历史落库。
- 不要用 early 卡固定 id 覆盖结果内容；结果必须是独立 media-result 卡，以便同会话多次生成都可回看。
