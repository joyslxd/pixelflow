---
topic: 重启 Gateway 后图片任务因瞬时授权丢失而失败
module: agent-runtime
date: 2026-09-02
keywords:
  - authorization_unavailable
  - TransientGenerationJobCredentialStore
  - generate_image_assets
---
## 结论摘要

GenerationJob 落在 SQLite，但用户 Authorization 只存在 Gateway 进程内存（默认 30 分钟）。重启 Gateway 后，排队中的 Job 会被 Worker 重新领取，凭据仓是空的，直接写成 `authorization_unavailable`。这不是 Content-App 又返回 HTML。

## 关键文件

- `backend/pixelflow/generation_jobs/credentials.py`
- `backend/pixelflow/generation_jobs/worker.py`

## 核心逻辑

1. 确认生成时 `put(job_id, authorization)`。
2. Worker `_start` 取不到凭据 → `indeterminate` + `authorization_unavailable`。
3. 正确恢复：`retry_failed_image_assets` 后重新确认生成，期间不要重启 Gateway。

## 注意事项

- 失败码与 `provider_response_not_json` 不同，不要再改 BORGRISE_BASE_URL。
