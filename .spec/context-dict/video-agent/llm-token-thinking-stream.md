---
topic: VideoAgent 真 LLM token 思考流
module: video-agent
date: 2026-08-11
keywords:
  - agent.thinking.delta
  - ThinkingStreamPublisher
  - AgentThinkingStream
  - IntakeThinkingResult
  - INTAKE_PLAN
  - INTAKE_VERDICT
  - channel=reasoning
  - channel=answer
  - 入场思考
  - 先思考后规划
  - needs_user_reply
  - public_goal
  - 打字机
---

## 结论摘要

入场顺序固定为：**先思考流 → 思考流同时产出用户可见 answer + 可调度 steps**。禁止并行「规划中」空卡；禁止本地规则抢先判定「成熟脚本」。

机器块升级为 `<<<INTAKE_PLAN>>>`（兼容旧 `INTAKE_VERDICT`）。`answer` 与 `public_goal` 必须相同；执行方案卡标题强制 `public_goal := answer`。

`needs_user_reply` / 仍缺生产字段时：**只写气泡结论，不落 Plan**。有 `steps` 时：**跳过 Planner 直接落库**；Planner 仅在无 steps 时兜底，且仍覆盖 `public_goal`。

前端 Thought：SSE delta + 打字机；完成后等追平再折叠；当前 Turn 始终用 live 组件，不因归档瞬间整段砸出。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`
- `backend/pixelflow/video_agent/entrypoint.py`（`_plan_from_intake_steps`）
- `backend/pixelflow/video_agent/planner/model.py`
- `backend/pixelflow/agent_runtime/service.py`（无 Plan 时仍收尾 Turn）
- `backend/app/gateway/routers/pixelflow_conversations.py`（SSE 轮询 ~80ms）
- `web/src/features/video-agent/AgentThinkingStream.tsx`
- `web/src/lib/supervisor/reducer.ts`

## 核心逻辑

1. `await stream_intake_thinking(workspace_digest, blocking_confirmation)` → `_submit_turn_after_thinking`
2. `needs_user_reply` / missing → patch workspace，`plan=None`，Turn 直接 completed
3. `thinking.steps` 非空 → `_plan_from_intake_steps`，`public_goal=answer`，不调 Planner
4. 无 steps 且 `entry_path=inspect` → inspect Plan
5. 否则 Planner 兜底；落库前仍强制 `public_goal=answer`
6. 大段 reasoning 后端按块推送；前端 rAF 打字机追平前保持展开

## 注意事项

- 事件在 deferred submit 后台写入，SSE 才能边想边推；勿改回 HTTP 同步阻塞整段思考
- DeepSeek 若一次吐完整 reasoning，仍靠切包 + 打字机做出流式感
- 无 Plan 追问回合必须 `_complete_video_agent_runtime_turn`，否则假排队
- 思考流失败兜底句不算权威 answer，此时允许沿用 notice/Planner fallback 文案
- **展示时序**：后端虽先 `thinking.completed` 再 `plan.created`，前端打字机追平前须 `holdActivePlanForThinking`，禁止边想边出执行方案卡
- **import_script**：DTO 必填 `markdown`；思考流 `arguments:{}` 时由 entrypoint/工具从 workspace 或本轮正文注入，禁止再报「工具参数无效」
- **刷新回显**：`agentThinkingHistory` 不能只靠 React 内存；Snapshot 须用 `fold_thinking_history_from_events` 投影 `thinkingHistory`，前端合并回显 Thought；answer 气泡走 `thinking-answer:{turnId}` 消息持久化
