---
topic: 单镜「确认并生成」误报全量启动数与场景包预览回填
module: video-agent
date: 2026-08-15
keywords:
  - 确认并生成分镜
  - resolveNativeSceneVideoBatchTotal
  - scene_video_progress
  - generatedSceneVideos
  - upsertNativeSceneVideoPreviewFromWorkspace
---

## 结论摘要

1. 用户点「确认并生成分镜 1」时，Turn 文案是 `确认并生成分镜视频（scene-1）`，后端只启 1 个 Operation；`scene_video_progress.total=1`，底栏可正确显示 0/1。
2. 对话 tip「已启动 14 个…」来自 FE：`progress?.total` 尚未投影时，用 `scene_packages.length` / `scenes.length` 兜底，把本批总数误写成全量包数。进度板与 tip 数据源不一致，造成「话术 14 / 进度 0/1」。
3. 回填路径：Workspace `variants.video_url` → `upsertNativeSceneVideoPreviewFromWorkspace` 写 `generatedSceneVideos` 到场景包卡 + 打开面板时再 merge。视频未成功落库前预览仍是参考图；另：点「分镜视频」卡原先清空 `selectedStoryboardMessageId`，进不了分镜面。

## 相关文件

- `web/src/features/video-agent/sceneVideoBatchTotal.ts`（`resolveNativeSceneVideoBatchTotal`）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`（re-export）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（tip、进度板初始化、打开 video_result）
- `web/tests/sceneVideoBatchTotal.test.mjs`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑

1. 本批 total 优先级：`scene_video_progress.total` → `generation_jobs` 数 → 已完成/失败数 → 生成中兜底 `1`；**禁止**用场景包全量长度。
2. `generate_scenes` 启动 effect：progress/jobs 晚到时用 `applySceneVideoProgress` 纠正首屏误建的占位总数。
3. 带 `videoScenePackages` 的 `video_result` 打开时设置 `selectedStoryboardMessageId`，与场景包卡一样进分镜面，便于 merge 预览。

## 注意事项

- 后端单镜解析仍靠括号 `scene-id`（见 `scene-video-fail-dedupe-and-single-scene-generate.md`）
- 历史会话里已落库的「已启动 14 个」文案不会自动改写，需等下一次 upsert（进度变化或刷新后 effect）或新一轮生成
- 当前 Operation 仍 `polling` 时 Workspace 无 `video_url`，预览不回填是预期；成功后依赖 Snapshot 轮询 + projector 写 variants
