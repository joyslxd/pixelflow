---
topic: 下游视频已成功但 Workspace 报 result_url_missing
module: agent-runtime
date: 2026-09-08
keywords:
  - provider_poll_video_result_url_missing
  - video_result_url_missing
  - http vitamazing
  - canonical_provider_media_url
  - reopen_missing_image_results
---
## 结论摘要

`provider_poll_video_result_url_missing` 表示 Content-App `/task/{id}/status` 已是 success，但 Adapter 抽不到可接受的成片 URL。EC 成片几乎都是 `http://*.vitamazing.top/...`；旧 `_first_video_url` 只收无 query 的 HTTPS，Worker 随后写成 indeterminate。图片侧早已走 `canonical_provider_media_url`，视频没有。前端上传还曾把 `www.vitamazing.top` 无条件升成 HTTPS，而该站点 TLS 不可用。

## 关键文件

- `backend/pixelflow/platform/content_app_url.py`
- `backend/pixelflow/capabilities/video_generation/providers/content_app.py`
- `backend/pixelflow/generation_jobs/projector.py`
- `backend/pixelflow/generation_jobs/worker.py` / `repository.py`
- `web/src/lib/contentAppOrigin.ts`（`canonicalUploadedMediaUrl`）

## 核心逻辑

1. `*.vitamazing.top` 无论入参 http/https 都写成 HTTP；TOS 仍升 HTTPS 并去掉查询串。
2. 视频 status 映射与图片一样走 canonical；成功补丁、digest 预览、inspect 都认 HTTP 白名单地址。
3. Worker 在 GenerationJob credential 已 discard 时仍可 poll VIDEO，凭据回退到进程内 Provider 任务租约或当前用户 `put_user`。轮询遇到 `provider_status_authorization_unavailable` 只改期，不写成终态。
4. `reopen_missing_image_results` 同时回放缺 URL 以及重启后误写成 `provider_poll_provider_status_authorization_unavailable` 的任务，不再次 `create_video` 扣费。

## 注意事项

- 不要对已有 `provider_job_id` 再点「确认生成」。
- Gateway 重启会丢掉任务租约；回放需要用户保持已登录页面，好让 `put_user` 补上当前浏览器授权。
- SQL 回放必须覆盖 `provider_poll_video_result_url_missing`，不能只认 `provider_result_missing`。
