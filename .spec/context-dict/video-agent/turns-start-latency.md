---
topic: turns/start 长时间无返回
module: video-agent / agent-runtime
date: 2026-08-07
keywords:
  - turns/start
  - VideoAgentEntrypoint
  - planner timeout
  - agent.plan.created
---
## 结论摘要
`POST .../turns/start` 曾在入口里 `await` DeepSeek planner（超时约 45s），HTTP 一直挂起；规划失败后才回落确定性计划。热路径已改为只落确定性短计划并推送 `agent.plan.created`，不在请求内等待 LLM。前端在登记 pending turn 时先发「已收到创作请求，正在生成执行方案…」。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/tests/test_video_agent_entrypoint.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑
1. `submit_turn`：创建/更新 workspace → `_deterministic_plan`（inspect + 可选 brainstorm_script）→ `save_plan` → `agent.plan.created` → 立即返回。
2. `self._planner` 仅保留装配，不在热路径调用；脚本生成由 Runner 异步执行工具步骤。
3. 前端 V2：`registrationStatus: pending` 后立刻 `appendPersistedSupervisorNotice` 给回执，再异步 `startTurn`。

## 注意事项
- 重启后端后旧 conversation resume 可能 404，需新建对话验证。
- `public_goal` 现为「处理视频创作请求：…」前缀，测试勿写死完整等号。
- 真正耗时在 Runner 执行 `brainstorm_script`，不应再堵在 `turns/start`。
