---
topic: 分镜 generation_mode=independent 不能送给供应商
module: agent-runtime
date: 2026-09-02
keywords:
  - create_video
  - generate_scenes
  - independent
  - video_generation_mode_unsupported
  - reference_mode_video
---
## 结论摘要

Workspace 分镜/Prompt Package 的 `generation_mode` 是 `independent/extend/reference`。Content-App 只要 `text_to_video/image_to_video/reference_mode_video/...`。`create_video` 把 `independent` 原样送给 `prepare_operation_request`，映射失败 `video_generation_mode_unsupported`，没有 GenerationJob。有参考图时应推断为 `reference_mode_video`；合同里的 `1080x1920` 要收成 `1080p`。

## 关键文件

- `backend/pixelflow/generation_jobs/requests.py`
- `backend/pixelflow/capabilities/video_generation/providers/content_app.py`
- `backend/pixelflow/agent_tools/video/scene.py`

## 核心逻辑

1. 已是供应商枚举则沿用。
2. Package `extend` 且有视频 → `extend_video`。
3. 其余按参考图/视频/音频推断，有图即 `reference_mode_video`。

## 注意事项

- 上传素材没有 `image_url`，要从 `source_material_id` 读 `materials.url`。
- 修复后需重启 Gateway，再让用户确认 `create_video`。
