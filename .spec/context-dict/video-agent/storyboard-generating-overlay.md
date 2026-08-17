---
topic: 分镜视频生成中镜头预览灰蒙版转圈
module: video-agent
date: 2026-08-15
keywords:
  - generatingSceneIds
  - SceneVideoGeneratingOverlay
  - StoryboardPanel
  - generationJobStatuses
  - 镜头预览
---

## 结论摘要

分镜视频 `polling` 时，场景包镜头主预览与对应缩略图盖半透明灰蒙版 + 转圈；缩略图标题显示「· 生成中」。生成中的 scene_id 由 LegacyWorkspace 从 Workspace `generation_jobs` / `edit_status=重新生成中` / `scene_video_progress.sceneId` 汇总后传入 `StoryboardPanel.generatingSceneIds`。

## 相关文件

- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/tests/videoSceneUiContract.test.mjs`

## 注意事项

- 有成片且无 busy job、且本批进度已完成时不蒙版，即使 `edit_status` 仍卡在「重新生成中」（并发生成整表覆盖后的残留态）
- **单镜重生例外**：保留旧 `video_url` 时仍须蒙版——靠 `busy` / 进行中 `progress.sceneId` / 点击乐观 `optimisticGeneratingSceneIds`
- 视频 URL 已回填且 job 终态后蒙版自动消失（依赖 Snapshot 轮询刷新 Workspace）
- `sceneVideosGenerating` 仅作 progress.sceneId 兜底，避免 jobs 尚未写入时完全无反馈
- 单镜 `generate_scenes` 启动会写 `scene_video_progress.scene_id`；多镜一批不写，避免误蒙
