---
topic: 刷新后参考图生成中假忙碌与任务板需处理
module: video-agent
date: 2026-08-09
keywords:
  - sceneAssetsGenerating
  - hasMaterializedScenePackageJob
  - reconcileStaleSceneAssetUiFlags
  - scene_package_job_resume_failed
  - 需处理
---
## 结论摘要
503 清掉 pending 后，聊天消息里仍可能持久化 `sceneAssetsGenerating=true`。刷新时前端看起来像「参考图生成中 / 自动重跑」，其实没有活跃 job。任务板因 `last_phase=scene_package_job_resume_failed` 命中 `/failed/` 显示「需处理」。现改为：无活跃 pending 时清掉假 spinner 并回到 awaiting model；`scene_asset_generation` 仅在有参考图时才算 materialized；resume_failed 映射为 waiting / awaiting_image_model。

## 关键文件
- `web/src/lib/scenePackageAssetUi.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（restoreConversation）
- `web/src/lib/workflowTaskBoard.ts`

## 核心逻辑
1. early structure card ≠ 生图完成
2. `reconcileStaleSceneAssetUiFlags`：无 active job → generating=false，无图则 awaitingModel=true，并解锁模型卡
3. 任务板：resume_failed → waiting；phase 纠偏为 awaiting_image_model / ready

## 注意事项
- 用户需刷新一次吃到修复；然后在模型卡重新确认即可真正重跑生图
- 后端内存 job 若已丢，只能重新发起，无法续旧 job id
- 未确认的 `scene_asset_model_options` 必须保留可点（`hasRecoverableArtifactAction`），否则会被后续场景包消息 supersede 成灰按钮
