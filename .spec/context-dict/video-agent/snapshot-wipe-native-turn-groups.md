---
topic: startTurn 前 refreshSnapshot 清空 native Turn
module: video-agent
date: 2026-08-13
keywords:
  - refreshSnapshot
  - snapshot.hydrated
  - AgentTurnGroup
  - hydrateNativeVideoAgentUiState
  - fold_thinking_history
---

## 结论摘要

用户补画幅/CTA 后再发 Turn，上一轮 Thought / import_script 卡片 / 回答气泡全部消失，
只剩新轮「已完成本轮处理」。

根因：`handleSupervisorTurn` 每次 `startTurn` 前 `refreshSnapshot()`；
`useSupervisorConversation` 在 `snapshot.hydrated` 时 **整表 reset** native Turn 组；
事件流只从 Snapshot resume sequence 续订，旧 Turn 事件不再重放。`video_agent_v2`
又禁用了 legacy thinking-answer 气泡，UI 只靠 native Turn 组，因此看起来像「对话没了」。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`refreshSnapshot` before startTurn）
- `web/src/hooks/useSupervisorConversation.ts`
- `web/src/features/native-video-agent/state/reducer.ts`（`hydrateNativeVideoAgentUiState`）
- `backend/pixelflow/video_agent/thinking_stream.py`（fold 原生 reasoning/response）

## 核心逻辑

1. Snapshot hydrate：**保留**同会话已投影 Turn（尤其 tools），再用 `agentThinkingHistory` 补正文。
2. Backend fold：把 `agent.reasoning_summary.*` / `agent.response.*` 折叠进 thinkingHistory，
   硬刷新后也能回填 Thought/回答（tool 卡片仍依赖内存或后续扩展）。

## 注意事项

- 勿再对同会话 Snapshot 调用 `resetNativeVideoAgentUiState`。
- 硬刷新后 tool 活动卡片仍可能缺失（事件未写入 thinkingHistory）；Thought/回答应可恢复。
