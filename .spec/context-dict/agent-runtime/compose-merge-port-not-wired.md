---
topic: 视频拼接未调用且校验过严
module: agent-runtime
date: 2026-09-02
keywords:
  - compose_or_export_video
  - video/merge
  - DeliveryOperationPort
  - scene_index
  - dirty_scene_ids
---
## 结论摘要

Gateway 日志里没有 `/api/video/merge`，不是 ffmpeg 失败，而是交付 Port 未装配，且前置校验把真实可交付工作区拦掉。已接上 Content-App 同步 merge；缺 `scene_index`、残留 dirty、多份 pending variant 不再误拒；仍在生成的镜头继续拒绝。

## 关键文件

- `backend/pixelflow/agent_tools/video/delivery.py`
- `backend/pixelflow/capabilities/video_delivery/providers/content_app.py`
- `backend/app/gateway/app.py`
- `backend/skills/skills/pixelflow-video-orchestration/SKILL.md`

## 核心逻辑

1. 单镜复用已有 HTTPS 成片，不发 HTTP；多镜 `POST {base}/video/merge`，body 仅 `videoUrls`，头带用户 Authorization、Idempotency-Key、canonical `modelType`。
2. URL 从 Workspace variant `artifact_ref` 解析，不进 Tool DTO。
3. 402「价格配置不存在」≠ 额度不足；空 402 仍暂停额度。
4. 同步合并默认读超时 1 小时（`BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT`）。

## 注意事项

- 改完必须重启 Gateway 才生效；轮询中的 GenerationJob 不要重启（授权只在内存）。
- 剪映工程包仍未装配。
- 日志关键字：`compose_or_export_video start`、`content-app video merge started/succeeded/business failed`。
