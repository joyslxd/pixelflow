---
topic: analyze_video 已接 Content-App 拆解但未回写工作区
module: video-agent
date: 2026-09-04
keywords:
  - analyze_video
  - decompose_video_to_storyboard
  - video_understanding
  - 参考视频拆解
---
## 结论摘要

Harness Tool `analyze_video` 已发布到 Manifest，Gateway 在 Content-App `base_url` 可用时装配 `ContentAppVideoUnderstandingAdapter`，提交 `POST /api/creative/decompose_video_to_storyboard`。当前只返回 `task_id`，没有 GenerationJob 轮询 `/api/task/{id}/status`，也不把分镜结果写回 VideoWorkspace。视频 Skill 未写该 Tool 名。

## 关键文件

- `backend/pixelflow/agent_tools/video/analyze.py`
- `backend/pixelflow/capabilities/video_understanding/providers/content_app.py`
- `backend/app/gateway/app.py`（`PIXELFLOW_VIDEO_UNDERSTANDING_ENABLED` 默认开）
- `CONTENT_APP_API_CALLS.md`（旧入口会轮询；V2 曾走 M06，现禁止恢复旧 M06 生成编排）

## 核心逻辑

1. Catalog 始终注册 `AnalyzeVideoTool`；未装配 Port 时 Observation 为 `unavailable`，不伪造成功。
2. Adapter 只 start 异步任务；`GenerationJobWorker` 只服务生图/生视频 Provider，不含视频理解。
3. 模型能否选到该 Tool 取决于 Manifest + Skill；`pixelflow-video-orchestration` 目前没有选 Tool 建议。

## 注意事项

- 不要为补齐轮询而恢复旧 `provider_jobs` / M06 拆解 Job。
- 补齐时应走 Gateway GenerationJob（或同类权威任务）轮询并白名单投影分镜，Sidecar 不直连 Content-App。
