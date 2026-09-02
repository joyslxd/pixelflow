---
topic: 图片任务已提交但前端无图
module: agent-runtime
date: 2026-09-01
keywords:
  - generate_image_assets
  - suspended_operation
  - provider_response_not_json
  - WorkspaceV2Panel
  - asset_registry
  - generation_job
---
## 结论摘要

Agent 说「已提交生成任务」只表示 `generate_image_assets` 创建了 GenerationJob，Run 进入 `suspended_operation`（看板文案「等待生成任务完成」）。真正出图要等 Gateway Worker 调 Provider 并回写 `state=ready`。本地这次 Worker 在 start 时收到 `200 text/html`，映射失败，资产已被写成 `failed`。前端公开 digest 不投影 `generation_job_id/status`，生成图缩略图也只给 `existing_material`，所以看板卡住、右侧看不到任务和图。

## 关键文件

- `backend/pixelflow/generation_jobs/worker.py`
- `backend/pixelflow/capabilities/image_generation/providers/content_app.py`
- `backend/pixelflow/video/workspace/digest.py`
- `web/src/features/agent-runtime/WorkspaceV2Panel.tsx`
- `web/src/features/agent-workspace/AgentTaskBoard.tsx`

## 核心逻辑

1. Tool 提交后 Run 挂起；Worker 失败会改 Workspace，但不自动推 SSE，浏览器停在挂起快照。
2. `_safe_v2_asset_registry` 只给 state/prompt，剥掉 job 字段；面板缩略图 `origin === existing_material` 才渲染。
3. 失败码 `provider_start_provider_response_not_json` 常见原因是 `BORGRISE_BASE_URL` 写成站点根 `https://test-video.borgrise.com`，登录校验会自动补 `/api`，生图 Provider 原先不会，于是打到前端 HTML。正确值是 `https://test-video.borgrise.com/api`。

## 注意事项

- 刷新后资产应变为失败，仍不会出现生成图。
- 修好 Provider 后再走 `retry_failed_image_assets` → `generate_image_assets`。
- 工作台进度：digest 投影 `generation_job_id/status` 并把 planned+job 显示为 generating；挂起时前端回读 Workspace，看板展示「正在生成 @女主人」。
