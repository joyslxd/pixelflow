---
topic: 合并成片成功但前端无播放器
module: agent-runtime
date: 2026-09-02
keywords:
  - merged_video
  - preview_url
  - compose_or_export_video
  - WorkspaceV2Panel
---
## 结论摘要

`compose_or_export_video` 已把 TOS 成片写入 Workspace `merged_video`/`outputs`，但公开 digest 原先不投影该字段。digest 现对白名单 TOS 投影 `merged_video.preview_url`，只在右侧工作台播放，不钉在对话流底部，避免挡住后续修改。

## 关键文件

- `backend/pixelflow/video/workspace/digest.py`
- `web/src/features/agent-runtime/workspaceV2.ts`
- `web/src/features/agent-runtime/WorkspaceV2Panel.tsx`

## 核心逻辑

1. 优先 `merged_video.merged_video_url`，其次 `outputs`/`deliveries` 的 mp4 HTTPS。
2. 仅 `.tos-cn-beijing.volces.com` / `.vitamazing.top` 进入 `preview_url`。
3. 改 digest 后需重启 Gateway；播放器只出现在工作台，对话流不长期占位。

## 注意事项

- 不要把 `task_id` 或非白名单 URL 放进 Snapshot。
- 分镜预览与合并成片是两套卡片，不要互相覆盖。
- 不要把合并成片播放器钉在对话流底部。
