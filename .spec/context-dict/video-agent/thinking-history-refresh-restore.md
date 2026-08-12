---
topic: 思考流刷新后消失与会话历史回显
module: video-agent
date: 2026-08-11
keywords:
  - thinkingHistory
  - fold_thinking_history_from_events
  - agentThinkingHistory
  - thinking-answer
  - snapshot.hydrated
  - 刷新恢复
---

## 结论摘要

思考 delta 早已写入 AgentEvent，但 Snapshot 未投影、前端 `agentThinkingHistory` 只在内存，刷新后 Thought 折叠区消失。现从持久化事件折叠 `thinkingHistory` 进 Snapshot，前端 hydrate 回显；结论气泡继续用 `thinking-answer:{turnId}` 消息落库。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`（`fold_thinking_history_from_events`）
- `backend/pixelflow/agent_runtime/service.py`（Snapshot `thinkingHistory`）
- `web/src/lib/supervisor/workspaceProjection.ts`
- `web/src/lib/supervisor/reducer.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑

1. Snapshot 扫描 `agent.thinking.*` 事件折叠为 `[{turnId,text,answer,status,...}]`
2. FE `projectSupervisorSnapshot` → `agentThinkingHistory`
3. `snapshot.hydrated` 合并历史；`thinking.completed` 同步归档
4. LegacyWorkspace 用 `resolveThinkingAfterMessageId` 挂回用户消息后

## 注意事项

- 勿再把 sessionStorage / 仅 React state 当思考历史权威
- 多轮锚点必须绑定触发该 Turn 的用户消息（`thinkingTurnAnchorsRef` / pending runId），禁止一律挂到最后一条用户输入（见 `thinking-plan-same-anchor.md`）
- 聊天正文消息本身已走 conversation messages；本修复补的是 Thought 活动块
