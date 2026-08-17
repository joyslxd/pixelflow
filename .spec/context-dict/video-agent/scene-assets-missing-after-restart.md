---
topic: 网关重启后分镜仍无全局素材图
module: video-agent
date: 2026-08-14
keywords:
  - generate_scene_assets
  - sceneAssetModelConfirmed
  - reconcileStaleSceneAssetUiFlags
  - 完成事件不唯一
  - 全局素材图
  - pendingScenePackageJob
---

## 结论摘要

用户确认生图模型后网关热重载/重启，内存中的 `GenerateSceneAssetsJobService` 任务被杀掉，Workspace 仍无 `global_assets` 图片。前端模型卡保持 `sceneAssetModelConfirmed=true`，投影不再显示「待选模」，也无法再点确认；残留 `pendingScenePackageJob` 还会直接拦掉 V2 Turn。表现为「查看分镜仍没有全局素材图」。

## 相关文件

- `web/src/lib/scenePackageAssetUi.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/video_agent/adapters/domain_jobs.py`

## 核心逻辑

1. `reconcileStaleSceneAssetUiFlags`：无活跃任务且无图时清假 generating，并解锁模型卡
2. V2 确认模型不再被旧 pendingScenePackageJob 拦截；无图时允许再次确认
3. `generate_scene_assets` 遇「完成事件/同步终态冲突」自动抬高 attempt 重开
4. 生图中轮询 Snapshot：tool=running **或** 进度卡/场景包 generating

## 注意事项

- 日志里周期性 `completion_dispatch` / `AgentRuntimeRecordConflictError` 是历史僵尸 Operation，与「能否再点选模」是两条线；解锁后新 attempt 才能真正生图
- 增量 `on_progress` 需网关已成功装配；重启后必须重新确认模型才会再跑生图
- 操作：刷新对话 → 选模卡应恢复可点 → 再确认 Seedream/image-2
