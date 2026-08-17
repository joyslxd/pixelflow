---
topic: 参考图应逐步写入分镜画布
module: video-agent
date: 2026-08-14
keywords:
  - on_progress
  - scene_asset_progress
  - global_assets
  - 查看分镜
  - make_scene_assets_workspace_progress
  - refreshSnapshot
---

## 结论摘要

V2 `generate_scene_assets` 领域层虽有逐张 `on_progress`，但网关 runner 未接线，Workspace 只在全部完成后由 Operation 投影写入。用户打开「查看分镜」会空等数分钟看不到任何图。

修复：每张参考图完成后增量 `apply_workspace_patch`（`global_assets` + `scene_asset_progress`）；FE 在 tool `running` 时每 3s `refreshSnapshot`，分镜与执行规划逐步刷新。

## 相关文件

- `backend/pixelflow/video_agent/adapters/domain_jobs.py`
- `backend/pixelflow/video_agent/adapters/scene_package_operation.py`
- `backend/app/gateway/app.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`

## 核心逻辑

1. Operation request 携带 `user_id/workspace_id/conversation_id`
2. `on_progress` → `make_scene_assets_workspace_progress` 乐观锁重试写 Workspace
3. FE：`nativeAssetsToolSignal.status === "running"` → 轮询 Snapshot
4. `sceneAssetProgress` 驱动执行规划第 3 步 detail

## 注意事项

- 进度回写失败只打日志，不中断生图
- 需重启网关使 runner 装配生效
- 旧会话若已在跑且无进度接线，只能等本批结束或重开生图
