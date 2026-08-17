---
topic: 视频资产包底部查看合并成片
module: video-agent
date: 2026-08-17
keywords:
  - merged_video
  - mergedVideoUrl
  - compose_or_export_video
  - StoryboardPanel
  - 查看合并后的视频
---

## 结论摘要

合并成功后成片 HTTPS URL 写入 Workspace `merged_video`（并同步 `deliveries`/`outputs` 的 `video_url`）。前端投影为 `mergedVideoUrl`，传入分镜资产包底部按钮「查看合并后的视频」，全屏 `<video>` 预览。

## 关键文件

- `backend/pixelflow/video_agent/tools/delivery.py`：MP4 成功时 `workspace_patch.merged_video`
- `web/src/features/video-agent/state/workspace.ts`：`projectMergedVideoUrl`
- `web/src/components/canvas/StoryboardPanel.tsx`：底部按钮 + 预览层
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`：把 `workspace.mergedVideoUrl` 传入资产包，并回填场景包卡 `artifact.mergedVideo`

## 核心逻辑

1. `ComposeOrExportVideoTool` 声明 `workspace_mutations` 含 `merged_video`。
2. 投影优先读 `payload.merged_video.merged_video_url`，其次 `outputs`/`deliveries` 中 `output_type=mp4` 的 HTTPS URL。
3. 仅 HTTPS URL 可预览；无成片时不展示按钮。

## 注意事项

- 场景包卡 early-return 须比较 merged URL，否则合并完成后卡片不回填 `mergedVideo`。
- 预览入口以 Workspace 权威投影为准，消息 artifact 仅作兜底。
- Snapshot 经 `cloneVideoWorkspaceProjectionState` 时必须保留 `mergedVideoUrl`，见
  `compose-success-merged-url-clone-drop.md`。
