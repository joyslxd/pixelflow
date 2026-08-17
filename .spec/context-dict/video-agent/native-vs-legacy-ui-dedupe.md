---
topic: video_agent_v2 禁止旧思考流/空 Plan/回答气泡与 native Turn 双显
module: video-agent
date: 2026-08-13
keywords:
  - AgentTurnGroup
  - AgentThinkingStream
  - thinking-answer
  - AgentPlanTimeline
  - 规划中
  - dual render
---

## 结论摘要

原生 VideoAgent 投产后，同一轮 SSE（`reasoning_summary` / `response`）仍被 Supervisor reducer
写入 `agentThinking`，LegacyWorkspace 再渲染 `AgentThinkingStream` + `thinking-answer` 气泡，
同时底部再渲染 `AgentTurnGroup`。用户会看到两套「Thought for…」和两份追问回答；空壳
observation plan（0 步 running）还会叠一张「规划中，正在生成执行步骤…」。

`video_agent_v2` 下：思考与最终回答只走 `AgentTurnGroup`；跳过 `thinking-answer` 落库；
隐藏无步骤且无确认/额度闸门的 Plan Timeline。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/native-video-agent/chat/AgentTurnGroup.tsx`
- `web/src/lib/supervisor/reducer.ts`（仍双写 agentThinking，仅 UI 去重）
- `web/tests/agentThinkingStream.test.mjs`

## 核心逻辑

1. `orchestrationMode === "video_agent_v2"` → `agentActivityBlocks` 不构建 `AgentThinkingStream`
2. 同模式下 `thinking-answer` effect 直接 return
3. 同模式下 `stepCount === 0 && !gate` 的 Plan（含 waiting 空壳）不渲染 Timeline
4. 有步骤或确认/额度闸门的 Timeline 仍保留

## 注意事项

- 事件层双写暂保留（兼容 Snapshot/`agentThinking`）；只收口渲染与气泡落库
- 刷新后 native Turn 细节依赖事件流，与既有 native 投影限制一致
- 勿再把过渡期「旧思考流仍在」当成常态；原生路径以 Turn 组为唯一 UI
