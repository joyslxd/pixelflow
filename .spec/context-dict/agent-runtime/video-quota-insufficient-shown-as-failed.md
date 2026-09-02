---
topic: 视频 402 被误标成额度不足（展示名当 modelType）
module: agent-runtime
date: 2026-09-02
keywords:
  - provider_quota_insufficient
  - create_video
  - generate_scenes
  - 402
---
## 结论摘要

对话 `2b29e4ec...` 两镜 GenerationJob 在 start 后约 400ms 写成 `failed` + `provider_quota_insufficient`，Content-App 对 `/api/video/reference-mode-video` 返回 HTTP 402。账户实际有额度。根因是合同 `video_model` 写成展示名 `Seedance 2.5`，而成功任务用的是目录 ID `seedance-2.5`。content-app 按 `modelType` 查价格档，找不到时也回 402；Adapter 把所有 402 当成额度不足。

## 关键文件

- `backend/pixelflow/generation_jobs/requests.py`
- `backend/pixelflow/video/workspace/payload.py`（`canonicalize_video_model`）
- `backend/pixelflow/capabilities/video_generation/providers/content_app.py`
- `backend/pixelflow/generation_jobs/worker.py`

## 核心逻辑

1. 展示名必须收成 `seedance-2.5` 再送 `modelType`。
2. 402 正文含「价格配置不存在」映射 `video_billing_profile_missing`，不是额度暂停。
3. 空 402 仍按额度不足处理。

## 注意事项

- 已失败 Job 的 request_json 仍可能是旧展示名；重试会按当前代码重新构造请求。
- 不要把所有 402 都解释成没钱。