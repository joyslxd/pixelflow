# PixelFlow AI Coding Context

## 项目定位
这是一个视频工作流项目，当前正在推进 V2 视频智能体（VideoAgent）。  
`LegacyWorkspace` 仍承载了大量历史页面能力，但已经逐步被 `video-agent` 契约和状态层接管。

## 关键事实（当前快照）
- 页面入口：`web/src/pages/WorkspacePage.tsx` → `features/video-agent/VideoAgentWorkspace.tsx`。
- `VideoAgentWorkspace` 目前仍返回 `LegacyWorkspace`（兼容承载期）。
- V2 视频资产与执行状态已在 `web/features/video-agent/*` 内建模与渲染组件中（`state/workspace.ts`, `AgentPlanTimeline.tsx`, `SceneEvidencePanel.tsx`, `VideoAgentStoryboardSurface.tsx`, `hooks/useVideoAgent.ts` 等）。
- 后端 VideoAgent 已有：`entrypoint`、`planner`、`executor`、`tools`、`adapters`、`runner`、`runtime`，以及相关快照/事件桥接。

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
