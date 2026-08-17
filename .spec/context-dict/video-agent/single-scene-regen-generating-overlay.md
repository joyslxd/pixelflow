---
topic: 单镜重生须保留灰蒙版直到新 video_url 回填
module: video-agent
date: 2026-08-17
keywords:
  - generatingSceneIds
  - SceneVideoGeneratingOverlay
  - optimisticGeneratingSceneIds
  - scene_video_progress.scene_id
  - 重新生成中
  - 确认并生成分镜
---

## 结论摘要

「确认并生成分镜 1」重生时镜头仍有旧成片。旧逻辑在「有成片且无 busy」时不蒙版，且 `generate_scenes` 启动时 `scene_video_progress.scene_id=null`，Snapshot 晚到前缩略图/主预览都没有灰蒙版，看起来像没进入生成态。

修复：

1. 单镜启动写入 `scene_video_progress.scene_id`
2. `busy` / 进行中 `progress.sceneId` / `edit_status=重新生成中`（本批未完成）即使有旧 URL 也蒙版
3. 点击后 `optimisticGeneratingSceneIds` 立刻蒙版；revision 推进且 job 终态后清除
4. `cloneVideoWorkspaceProjectionState` 保留 `generation_jobs`，避免 busy 被克隆丢掉

## 关键文件

- `backend/pixelflow/video_agent/tools/scene.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`
- `web/tests/videoSceneUiContract.test.mjs`
- `web/tests/videoAgentWorkspaceProjection.test.mjs`

## 注意事项

- 多镜一批启动仍不写单一 `scene_id`，靠各镜 `generation_jobs` busy 蒙版
- 完成后靠 Snapshot + projector 回填新 `video_url`；蒙版随 job 终态消失
