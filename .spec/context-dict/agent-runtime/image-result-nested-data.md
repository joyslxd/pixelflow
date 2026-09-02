---
topic: 图片成功结果包在 result.data 里
module: agent-runtime
date: 2026-09-02
keywords:
  - generate_image_assets
  - provider_result_missing
  - result.data
  - content_app
  - image_result_url_missing
  - 等待调度
---
## 结论摘要

Content-App 图片 URL 在 `data.result.data`。旧映射抽不到仍标成功，Worker 写成 `provider_result_missing`。工作区可能仍停在 `generating + queued`（界面「等待调度」），失败投影没写上，所以 `retry_failed_image_assets` / `generate_image_assets` 都接不住。供应商任务还在，应回放 status 补结果，不要重新 generate 计费。

## 关键文件

- `backend/pixelflow/capabilities/image_generation/providers/content_app.py`
- `backend/pixelflow/generation_jobs/worker.py`
- `backend/pixelflow/generation_jobs/repository.py`
- `backend/pixelflow/generation_jobs/projector.py`

## 核心逻辑

1. Adapter 拆 `result.data`；成功无 URL → `image_result_url_missing`。
2. Worker 会把 `indeterminate + provider_result_missing` 且尚无成功任务的图片 Job 重新打开轮询。
3. 成功投影必须写 `generation_job_status=succeeded`，否则界面仍显示「等待调度」。

## 注意事项

- 不要对已有 `provider_job_id` 的缺结果任务再调 `generate_image_assets`（会新扣费）。厨房 `asset_scene_01` 已有后续成功任务，旧缺结果 Job 不得回放。
- 回放依赖当前用户浏览器授权；页面保持登录即可。
