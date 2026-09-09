---
topic: EC 生图成功但 PixelFlow 报 result_url_missing
module: agent-runtime
date: 2026-09-07
keywords:
  - provider_poll_image_result_url_missing
  - image_result_url_missing
  - http TOS
  - creator.vitamazing.top
  - canonical_provider_media_url
---
## 结论摘要

`provider_poll_image_result_url_missing` 表示 Content-App `/task/{id}/status` 已是 success，但 Adapter 抽不到可接受的图片 URL。EC 的 Content-App 常返回 `http://` TOS 或 `http://creator.vitamazing.top/...`；创作页能显示照片，PixelFlow 旧逻辑只收 `https://`，于是写成失败。前端上传早已把 TOS HTTP 升成 HTTPS。

## 关键文件

- `backend/pixelflow/platform/content_app_url.py`（`canonical_provider_media_url`）
- `backend/pixelflow/capabilities/image_generation/providers/content_app.py`
- `backend/pixelflow/generation_jobs/projector.py`
- `backend/pixelflow/generation_jobs/repository.py`（回放缺 URL 终态）
- `web/src/api/contentAppAssets.ts`（上传侧 HTTP→HTTPS）

## 核心逻辑

1. TOS 主机 `.tos-cn-beijing.volces.com` 的 HTTP 升为 HTTPS，去掉查询串。
2. 已登记 `*.vitamazing.top` **一律写成 HTTP**（该站点无可用 TLS；前端误升的 HTTPS 也要降回来）。
3. 其它公网 HTTP 仍拒绝。
4. Worker 会回放 `provider_poll_image_result_url_missing` / `provider_result_missing` 且已有 `provider_job_id` 的图片 Job，不再次 `text_to_image` 扣费。

## 注意事项

- 不要对已有供应商任务再点「重新生成」除非用户明确要新计费。
- 回放仍需要当前用户登录授权；Gateway 重启后需用户再打开已登录页面。
- 不要把 TOS 预签名查询串写入 Snapshot。
