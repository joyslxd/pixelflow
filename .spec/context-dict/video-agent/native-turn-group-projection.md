---
topic: Native Video Agent Turn 组事件投影
module: video-agent
date: 2026-08-12
keywords:
  - native-video-agent
  - AgentTurnGroup
  - reduceNativeVideoAgentEvent
  - agent.tool
  - agent.response
---

## 结论摘要

P0-2.3 用独立 `native-video-agent` 状态投影 Turn 组，不替代现有 `agent.thinking.*` /
`AgentPlanTimeline` 兼容渲染。SSE 事件经 `useSupervisorConversation` 双写：
Supervisor reducer + `reduceNativeVideoAgentEvent`。

## 关键文件

- `web/src/features/native-video-agent/state/{contracts,reducer,selectors}.ts`
- `web/src/features/native-video-agent/chat/AgentTurnGroup.tsx`
- `web/src/hooks/useSupervisorConversation.ts`（`nativeUiState`）
- `web/src/components/chat/ChatPanel.tsx`（`nativeTurnGroups`）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑

1. 同 `conversation_id` + `turn_id`；`sequence <= lastSequence` 忽略。
2. 展示顺序：思考 → 计划(≤3) → 活动 → 结果卡槽 → 回答。
3. Snapshot hydrate / conversation.reset 清空 native Turn 组，后续事件再补齐。

## 注意事项

- `video_agent_v2` 渲染已收口：禁止再叠旧 `AgentThinkingStream` / 空壳 Plan /
  `thinking-answer` 气泡（见 `native-vs-legacy-ui-dedupe.md`）。
- 原生链事件仍是 `agent.reasoning_summary.*` / `agent.tool.*` / `agent.response.*`。
- Snapshot hydrate **不得清空**已投影 Turn；用 `hydrateNativeVideoAgentUiState` 合并
  `agentThinkingHistory`（见 `snapshot-wipe-native-turn-groups.md`）。
- 硬刷新后 tool 卡片仍可能不全；Thought/回答靠 fold 原生事件恢复。
