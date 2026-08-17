# PixelFlow AI Coding Context

## 项目定位
这是一个视频工作流项目，当前正在推进 V2 视频智能体（VideoAgent）。  
`LegacyWorkspace` 仍承载了大量历史页面能力，但已经逐步被 `video-agent` 契约和状态层接管。

## 关键事实（当前快照）
- 页面入口：`web/src/pages/WorkspacePage.tsx` → `features/video-agent/VideoAgentWorkspace.tsx`。
- `VideoAgentWorkspace` 目前仍返回 `LegacyWorkspace`（兼容承载期）；原生 UI 在 `features/native-video-agent/`。
- **视频执行只走原生 VideoAgent**（`create_video_agent` / Thin Entrypoint / Tool Gateway）。
- **已硬删除：** Intake JSON Planner、`run_plan`、对外 `/agent/flows/video*` 与剪映 Job **路由模块文件**、前端 Job HTTP 客户端。
- `LegacyWorkspace.tsx` 已拆出 `legacyWorkspaceTypes` / `Helpers` / `LegacyVideoJobs`（约 9.9k 行兼容壳）。
- 历史 `frontend_v2` 会话首次 Turn/脚本保存时升级为 `video_agent_v2`。
- 后端 VideoAgent：`agent.py`、`native_invoke.py`、`tool_gateway.py`、`tools/*`、`workspace/*`、`events/*`。

## 目录地图（常改动区域）
- 后端：`backend/pixelflow/video_agent/*`, `backend/pixelflow/agent_runtime/*`, `backend/app/gateway/*`
- 前端：`web/src/features/video-agent/*`, `web/src/features/legacy-workspace/LegacyWorkspace.tsx`, `web/src/lib/supervisor/*`, `web/src/pages/WorkspacePage.tsx`
- 文档与计划：`docs/superpowers/plans/2026-08-04-unified-video-agent-v2.md`, `docs/pixelflow-agent-skill-flow-latest-design.md`

## 约定
- 优先复用已有能力，不主动重写 `LegacyWorkspace.tsx` 全量代码。
- V2 目标是“路由归属清晰 + 状态可恢复 + 公开事件不泄漏内部参数/推理”。
- 统一执行口径：
  - 统一入口与归属以 VideoAgent 链路为优先。
  - 前端以 snapshot/event 投影驱动显示，不依赖临时内部细节。
  - 确认类动作仅走明确的确认/取消 API，不伪造自然语言 turn。
- 测试优先：涉及共享状态/投影/序列化变更时，同步更新相关前后端测试。

## 本次任务状态提示
- `LegacyWorkspace`=兼容承载层；V2 页面边界仍在收口中。
- 任何时间点提交前，建议同步检查：
  - `git status`
  - 计划文件里的完成项状态
  - 关键测试（至少涉及 `web/tests/videoAgentWorkspaceProjection.test.mjs`, `web/tests/supervisorRuntimeNotice.test.mjs`, `backend/tests/test_video_agent_*.py` 相关集）
