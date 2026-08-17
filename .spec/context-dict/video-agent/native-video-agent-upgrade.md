---
topic: Native Video Agent 改造基线与删除清单
module: video-agent
date: 2026-08-12
keywords:
  - native-video-agent
  - create_deerflow_agent
  - create_video_agent
  - frontend_v2
  - hard-delete
  - flows/video
---

## 结论摘要

权威设计：`docs/superpowers/specs/2026-08-12-native-video-agent-design.md`。  
实施计划：`docs/superpowers/plans/2026-08-12-native-video-agent.md`。

改造目标是单一 DeerFlow 原生 Tool-calling Agent；Intake / JSON Planner / Plan Runner /  
`/agent/flows/video*` Job HTTP / 前端 pending Job 轮询全部硬删除，无 feature flag 双轨。  
V2.1 的 Workspace、Confirmation、Operation、额度、revision 边界保留。

业务 Tool 名单已在 `video_agent/tools/*` 齐备，缺的是 `create_video_agent` 装配、  
`execute_tool_call`、Tool Gateway Middleware 与旧决策层拆除。

## 关键文件

- 设计：`docs/superpowers/specs/2026-08-12-native-video-agent-design.md`
- 计划：`docs/superpowers/plans/2026-08-12-native-video-agent.md`
- DeerFlow 工厂：`backend/packages/harness/deerflow/agents/factory.py`
- 现决策层：`entrypoint.py`、`planner/`、`thinking_stream.py`、`executor/service.py`、`runner.py`
- 旧路由：`backend/app/gateway/routers/pixelflow_video.py`、`pixelflow_jianying_draft.py`
- 前端 Job：`web/src/lib/api.ts`、`LegacyWorkspace.tsx`

## 核心逻辑

1. `create_video_agent()` → `create_deerflow_agent(middleware=全量接管)`，禁止 `make_lead_agent`。
2. Registry → StructuredTool；Gateway 强制确认/额度/revision；Executor 只执行单次 Tool Call。
3. Operation 终态用内部 resume Turn 唤醒 Agent；Plan 只观察。
4. 历史 `frontend_v2` 首次操作同事务升级；失败不部分写入。

## 注意事项

- 与 V2.1「暂不删旧 HTTP」冲突时，以 2026-08-12 设计为准（硬删）。
- P0-5 前不要拆路由；先让原生链行为覆盖。
- 剪映 / 场景包领域服务可留，只删对外 Job 路由与前端直调。
- L3 已拍板（2026-08-12）：硬删旧 HTTP；新会话只原生；剪映走 Tool；Memory 复用 DeerFlow；按 P0-1→P0-5 推进。
- P0-1.1～P0-1.5 + P0-2.1 已落地：Workspace 上下文 Middleware、`update_video_plan`、观察 Plan 自动补步、业务 Tool 上限 3。
- 观察 Plan 允许 `RUNNING` 空 steps；Tool 上下文用 contextvars，`plan_id/step_id` 必须成对。
- P0-2.2 已落地：`AgentEventType` 增 native 事件；`events/native.py` + `NativeAgentEventPublisher`；
  `VideoProgressMiddleware` 发 `agent.tool.*`；invoke 结束发 `agent.response.completed`；
  FE contracts/reducer 最小认新事件（tool/op/artifact 先推进 resume）。
- P0-2.3 已落地：`web/src/features/native-video-agent/` Turn 组状态/UI；
  `useSupervisorConversation` 并行投影 nativeUiState；`ChatPanel.nativeTurnGroups` 最小接入。
- P0-3.1～3.3 已落地：Gateway 强制确认/额度/revision；`native_pending_confirmation` /
  `native_approved_confirmation`；Operation 终态 `NativeOperationResumeHandler` 内部 resume Turn；
  FE `cards/*`（Confirmation/Quota/Operation/Error）。
- P0-4 已落地：`legacy_upgrade.py` 首次 Turn/脚本保存升级并在模式切换失败时 discard Workspace；
  FE `native-video-agent/canvas/*`（VideoCanvasShell / ArtifactCanvasRouter / 六类 Canvas）；
  `LegacyWorkspace` 在 `video_agent_v2` 经 Router 装配；单镜 dirty helpers 与「重新生成完成」文案。
- P0-5 已落地：删除 `planner/` 与 Intake 决策；`run_plan` 硬删；Gateway 停挂 `/agent/flows/video*`；
  FE Job HTTP stub throw + LegacyWorkspace 防护。详见 `p0-5-hard-delete.md`。
