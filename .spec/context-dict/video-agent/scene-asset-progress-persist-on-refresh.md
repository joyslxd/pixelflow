---
topic: 参考图进度刷新丢失与选模卡复活
module: video-agent
date: 2026-08-10
keywords:
  - persist_scene_assets_progress
  - upsertPersistedChatMessage
  - sceneAssetModelConfirmed
  - markConfirmedSceneAssetModelOptions
  - append client_message_id
---
## 结论摘要
参考图进度卡用固定 `client_message_id` 反复 `append`：后端对同 message_id **不覆盖**，DB 停在首次无图/少图快照；刷新后图片丢失。模型确认只改本地 `setMessages`，未 PATCH，刷新后选模卡重新可点。现：后端每张图 `persist_scene_assets_progress`（upsert 进度卡 + patch context）；前端进度/tip 走 `upsertPersistedChatMessage`（先 PATCH 后 create）；确认模型立即落库；恢复时 `markConfirmedSceneAssetModelOptions`。

## 关键文件
- `backend/pixelflow/generate/media_history.py`
- `backend/app/gateway/routers/pixelflow_video.py`（`_run_scene_asset_job` on_progress）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/lib/scenePackageAssetUi.ts`

## 核心逻辑
1. `upsert_conversation_message`：update by client_message_id，404 再 append
2. 进度卡 id：`scene-package-job:scene_asset_generation:{job_id}`（与 FE 对齐）
3. 有图才写进度；终态仍写 `media-result:scene_assets:{job_id}`

## 注意事项
- 开发热重载仍会丢内存 Job，但已生成图应能从消息/context 恢复
- 旧会话若从未落过进度，刷新仍可能丢图，需重跑参考图
