---
topic: V2 确认生成分镜视频后无进度
module: video-agent
date: 2026-08-14
keywords:
  - generate_scenes
  - scene_video_progress
  - completion_dispatch
  - AgentRuntimeRecordConflictError
  - PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION
  - 分镜视频进度
  - upsertNativeSceneVideoPreviewFromWorkspace
---

## 结论摘要

「确认并生成分镜视频」后 Agent 已回报「已启动 N 个」，但底栏仍钉在「执行规划 · 视频资产包」、页面无分镜视频进度/预览，根因有多层：

1. **FE**：V2 走 Turn → `generate_scenes`，旧 `pendingVideoJob` / `video_progress` Job 轮询已删；未监听 `generate_scenes`，也不清资产包进度板。
2. **BE 多 Operation**：一批分镜挂多个 Operation 到同一步；首镜完成就把步骤标 COMPLETED，后续 completion 撞 `AgentRuntimeRecordConflictError`，`video_url` 从未写回 Workspace。
3. **僵尸 delivering**：旧 `prepare/assets` 成功/失败完成事件在步骤非 RUNNING 时冲突刷屏，饿死分镜 status 轮询。
4. **status JWT 过期（本次「生成完了却不能预览」直接原因）**：网关用 `/tmp/pf_token.txt` → `PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION` 轮询 content-app；JWT `exp` 过期后 status 403，Operation 永久 `polling`，Workspace 无 URL。供应商侧其实已 `completed` 且有 `video_url`。

修复：每镜成功先投影 `generation_jobs/variants/scene_video_progress`，全部终态后再 native resume；prepare/assets/分镜终态（含 failed）在无 Plan / 无工作区 / 步骤非 RUNNING 时 soft-ack；FE 切到「执行规划 · 分镜视频」并轮询 Snapshot / early 预览卡。本地需保持有效 status Bearer。

## 相关文件

- `backend/pixelflow/video_agent/operations/projector.py`
- `backend/pixelflow/video_agent/operation_resume.py`
- `backend/pixelflow/video_agent/tools/scene.py`
- `backend/app/gateway/app.py`（`PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION` / `_FILE`）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`

## 核心逻辑

1. `build_scene_generation_success_patch`：按 `job_id` 增量写 URL + `scene_video_progress`
2. `VideoAgentOperationResumer`：`generate_scene:*` 且仍有 polling Job → 只投影、不唤醒；`_is_soft_ackable_scene_completion` 吞掉陈旧成功/失败终态
3. FE：`nativeGenerateScenesToolSignal` → 清资产包板 → `createSceneVideoProgressSteps`；3s `refreshSnapshot`；`upsertNativeSceneVideoPreviewFromWorkspace`（有 URL 强制出「分镜视频」卡）
4. status 鉴权：优先每次轮询重读 `PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION_FILE`，避免启动时注入的过期 JWT 永久卡住回填

## 注意事项

- 用户说「生成完了」时，先查 `pixelflow_agent_operations` 是否仍 `polling` + Workspace 是否有 `variants[].video_url`；再查 status JWT 是否过期（`/tmp/pf_token.txt`）
- Gateway 优先读 `PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION_FILE`（每次 status 轮询重读），本地把有效 Bearer 写入该文件后**无需重启**即可恢复回填；未配置 FILE 时仍读环境变量（启动时注入的旧 JWT 会一直 403）
- 刷新方式：浏览器 `localStorage.Authorization` 或 `/agentfrontend/#/auth-token` 里的有效 token → 写入 `/tmp/pf_token.txt`（可无 `Bearer ` 前缀）
- 「视频场景包」= 结构/参考图；成片片段看独立「分镜视频」预览卡 + 底栏「执行规划 · 分镜视频」；打开分镜面后由 Workspace `variants.video_url` merge 到镜头预览
- 旧 Job HTTP `upsertEarlySceneVideoCard` 仍保留给 frontend_v2 兼容路径
