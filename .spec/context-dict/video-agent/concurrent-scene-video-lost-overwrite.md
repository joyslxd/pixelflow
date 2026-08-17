---
topic: 并发生成后场景包仍转圈不回显视频
module: video-agent
date: 2026-08-15
keywords:
  - scenes_replace
  - merge_scenes_by_id
  - generate_scenes
  - build_scene_generation_success_patch
  - storyboardGeneratingSceneIds
  - video_url
---

## 结论摘要

1. 根因：`apply_workspace_patch` 对 `scenes` 整键替换。分镜 5 完成后，分镜 6 的 `generate_scenes`（或冲突重试的旧补丁）用启动时快照整表写回，把分镜 5 的 `video_url`/终态盖回 `polling`/`重新生成中` → 蒙版不消、预览无视频。
2. 修复：默认按 `scene_id` 合并 `scenes`/`scene_packages`；`generate_scenes` 与 completion/failure projector 只写变更镜；`prepare_scene_packages` / `generate_scene_assets` 用 `scenes_replace=True` 全量重建。
3. FE 兜底：有成片且无 busy job 时不蒙版；`mediaUrl` 回退到任意带 URL 的 variant。

## 相关文件

- `backend/pixelflow/video_agent/workspace/repository.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/pixelflow/video_agent/operations/projector.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`

## 注意事项

- 已丢成片的历史会话需重新生成该镜或等新的完成事件；本修复只防后续并发覆盖。
- 冲突重试仍复用同一补丁内容时，合并语义才能保住其它镜；整表 replace 仍会丢数据。
- `scenes_replace` 不得落进 payload（`_updated_workspace` 会 pop）。
