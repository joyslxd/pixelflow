---
topic: 分镜视频 402 价格档缺失且 HTTP 角色图被丢掉
module: agent-runtime
date: 2026-09-07
keywords:
  - create_video
  - reference-mode-video
  - video_billing_profile_missing
  - seedance-2.5
  - vitamazing
  - image_urls
---
## 结论摘要

对话 `4ac9669f...` 五镜 GenerationJob 在 start 时全部写成 `indeterminate` + `provider_start_video_billing_profile_missing`。Prompt Package 不是原因。Content-App `POST /api/video/reference-mode-video` 返回 HTTP 402 且正文含「价格配置不存在」；没有供应商 taskId。同时 `build_scene_generation_request` 曾只收 HTTPS，EC 角色图 `http://www.vitamazing.top/...` 被丢掉，每镜实际只带上 1 张 HTTPS 上传素材。

## 关键文件

- `backend/pixelflow/generation_jobs/requests.py`
- `backend/pixelflow/platform/content_app_url.py`
- `backend/pixelflow/capabilities/video_generation/providers/content_app.py`
- `backend/tests/test_scene_material_references.py`

## 核心逻辑

1. Workspace `independent` 已正确映射为 `reference_mode_video`；`Seedance 2.5` 已收成 `seedance-2.5`；`1080x1920` 已收成 `1080p`。
2. 402「价格配置不存在」不是额度不足，也不是 Prompt 超长。
3. 参考图必须走 `canonical_provider_media_url`：TOS HTTP 升 HTTPS，vitamazing 允许 HTTP。

## 注意事项

- 2026-09-07 23:21 同一对话又确认了一次，5 镜再次 402，请求仍是 `seedance-2.5` + `1080p` + 时长 4/5/6，每镜仍只有 1 张 HTTPS 上传图。
- 本机 Gateway 若未重启，HTTP 角色图修复不会生效；即使修好参考图，EC 缺价格档仍会 402。
- 界面不要再提示「请改成 seedance-2.5」：本合同已经是该目录 ID。
- 已失败 Job 没有 provider_job_id，再点确认会重新 POST。
- 不要把界面「成片失败」理解成模型画出了坏视频。
