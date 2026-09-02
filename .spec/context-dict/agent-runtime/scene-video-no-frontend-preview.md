---
topic: 分镜成片生成后工作台没有播放器
module: agent-runtime
date: 2026-09-02
keywords:
  - video_url
  - preview_url
  - prompt_packages
  - WorkspaceV2Panel
  - scene preview
---
## 结论摘要

GenerationJob 成功会把 `video_url` 写进 `scenes`。公开 digest 现在对白名单 TOS 域投影 `preview_url`，工作台 `<video src>` 直连播放。`GET .../scenes/{id}/preview` 只返回 `{url}`，不再把整段 mp4 经 Gateway 下载（否则会一直 pending）。

## 关键文件

- `backend/pixelflow/video/workspace/digest.py`
- `backend/app/gateway/routers/pixelflow_conversations.py`
- `web/src/features/agent-runtime/WorkspaceScenePreview.tsx`
- `web/src/features/agent-runtime/workspaceV2.ts`

## 核心逻辑

1. 成片事实在 `scenes[].video_url` / 已选 `variants` / 成功 `generation_jobs`。
2. 只公开 `.tos-cn-beijing.volces.com` / `.vitamazing.top` 的 HTTPS。
3. 图片缩略图仍走 Gateway 代理；成片不走代理。

## 注意事项

- 改完需重启 Gateway；刷新后播放器应直接请求 TOS。
- 非白名单主机不会进入 Snapshot，前端也不会渲染。
- 失败分镜没有 `preview_url`，只显示失败状态。
