---
topic: P0-5 旧链路硬删除结果
module: video-agent
date: 2026-08-12
keywords:
  - hard-delete
  - planner
  - run_plan
  - flows/video
  - frontend Job API
---

## 结论摘要

P0-5 已落地：生产视频执行只剩原生 VideoAgent。

- 后端：删除 `planner/`；`stream_intake_thinking` 已删；`run_plan`/`confirm_step` 抛错；
  Runner 只 invoke 原生 Agent；Gateway 不再 `include_router` 视频/剪映 Job 路由。
- `workspace_digest` 迁到 `video_agent/workspace/digest.py`。
- 前端：`api.ts` 旧 `/flows/video` 客户端统一 throw；`LegacyWorkspace` 禁止恢复轮询与
  旧按钮直调，提示走对话 Agent。
- 历史升级仍由 `legacy_upgrade.py` 负责。

## 关键文件

- `backend/pixelflow/video_agent/entrypoint.py`、`runner.py`、`executor/service.py`
- `backend/app/gateway/app.py`
- `web/src/lib/api.ts`、`web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- 计划：`docs/superpowers/plans/2026-08-12-native-video-agent.md`

## 核心逻辑

1. Thin Entrypoint 只建 Workspace + 空步观察 Plan。
2. Tool 经 Gateway 确认/额度/revision 后 `execute_tool_call`。
3. 旧 HTTP 模块文件可保留但不得挂载；领域 generate/jianying 仍供 Adapter 使用。

## 注意事项

- `api.ts` 方法名仍在（stub throw），避免大面积类型破坏；后续可再物理删除符号。
- 完整 Golden Journey 手工联调仍建议按设计 §15.3 走一遍。
- IntakeThinkingResult 等符号可能仍残留在 thinking_stream 供历史测试/兼容，但不再驱动执行。
