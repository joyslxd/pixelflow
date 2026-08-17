---
topic: 分镜视频失败展示与参考图未带上
module: video-agent
date: 2026-08-14
keywords:
  - failed_scenes
  - generation_jobs
  - image_urls
  - mentions
  - global_assets
  - shot_description.text
  - upsertNativeSceneVideoPreviewFromWorkspace
---

## 结论摘要

1. **失败镜不展示**：V2 early「分镜视频」卡硬编码 `failed_scenes: []`；失败 Operation 只 soft-ack / fail_step，未写回 `generation_jobs.error`。页面只看到 12/14，不知道哪镜失败。
2. **参考图/@错位**：`generate_scenes` 优先读脏字段 `prompt`（常是「故事线+镜头描述」拼接），且 mentions 常无 `image_url`；资产图已在 `global_assets`，但未按 `reference_asset_ids` 回填，结果误走 `text_to_video`。分镜面板上方表格显示 `@character-1`，下方编辑器 chip 显示 `@安然`，看起来像「编辑框没回到镜头描述」。

## 相关文件

- `backend/pixelflow/video_agent/operations/projector.py`（`build_scene_generation_failure_patch`）
- `backend/pixelflow/video_agent/operation_resume.py`
- `backend/pixelflow/video_agent/adapters/scene_operation.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/components/canvas/SceneMentionEditor.tsx`

## 核心逻辑

1. 失败完成事件 → 写 `generation_jobs.status/error` + `edit_status=重新生成失败`；进度把终态（成功/失败）都计入 completed
2. FE 从 `generationFailures` 填 `failed_scenes`，卡片展示「分镜 N · 失败原因」
3. 组装 Provider 请求：`shot_description.text` 优先于 `prompt`；`image_urls` = 镜头字段 + mentions + `global_assets` 按 asset_id
4. `patch_scene` 对齐 mentions 时补 `image_url` 并同步 `image_urls`
5. 结构化镜头描述展示时把 `@asset_id` 映射为 `@展示名`

## 注意事项

- 历史会话若 job 仍标 polling 但 Operation 已 failed，需按 stage digest 回填一次（本地已对当前对话做过）
- 重新「确认并生成分镜视频」后才会用上参考图补齐逻辑；已生成的成功镜不会自动重跑
- 编辑器序列化依赖 chip 的 `data-mention-image-url`；保存 Turn 只传 `reference_asset_ids`，必须靠服务端从 global_assets 补图
