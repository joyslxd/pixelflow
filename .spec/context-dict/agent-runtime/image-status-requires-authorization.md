---
topic: 图片 status 轮询必须带用户授权
module: agent-runtime
date: 2026-09-02
keywords:
  - generate_image_assets
  - authorization
  - content_app
  - 401
  - polling
---
## 结论摘要

Content-App 图片 start 带 Authorization 能建任务，status 以前不带头所以一直 401。现在 status 优先用 GenerationJob 瞬时凭据，缺省回退当前用户浏览器租约；Gateway 与视频共用同一 Authorization Store。

## 关键文件

- `backend/pixelflow/capabilities/image_generation/providers/content_app.py`
- `backend/pixelflow/generation_jobs/worker.py`
- `backend/app/gateway/app.py`

## 核心逻辑

1. start 成功后 `put_job`。
2. Worker 轮询图片时传入 `authorization`；进程重启后可借 `put_user` 的浏览器授权。
3. 日志里 `/task/{id}/status` 应为 200，不再是 401。

## 注意事项

- 改完需重启 Gateway；当前页还开着时用户租约还在，原 polling 任务有机会继续，不要先 retry 再重启。
