---
topic: 合并成功后成片 URL 被前端克隆丢掉
module: video-agent
date: 2026-08-17
keywords:
  - mergedVideoUrl
  - cloneVideoWorkspaceProjectionState
  - compose_or_export_video
  - video_result
  - 成品视频
---

## 结论摘要

工具侧已返回「MP4成片已生成」且 Workspace 写入 `merged_video` 后，前端 Snapshot
经 `cloneVideoWorkspaceProjectionState` 再投影时未带回 `merged_video`，导致
`mergedVideoUrl` 恒为 null：资产包无「查看合并后的视频」、对话无成品卡。

## 关键文件

- `web/src/features/video-agent/state/workspace.ts`：克隆补 `merged_video`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`：成片 → `video_result` 卡
- `web/src/components/chat/MessageBubble.tsx`：场景包卡展示成品预览

## 核心逻辑

1. `projectVideoWorkspaceSnapshot` 能从 payload 解析 URL。
2. Reducer `cloneProjection` 走克隆函数；必须把 `mergedVideoUrl` 写回 payload。
3. 成片出现后 upsert `video_result`，并回填场景包 `artifact.mergedVideo`。

## 注意事项

- 模型文案仍可能误说「请再确认」；以 Workspace / 卡片预览为准。
- 刷新后仍依赖 Snapshot 含 `merged_video`/`outputs.video_url`。
