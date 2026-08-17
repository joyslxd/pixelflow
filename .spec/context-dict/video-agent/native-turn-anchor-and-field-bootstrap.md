---
topic: Native Turn 锚点与补字段确定性写入
module: video-agent
date: 2026-08-13
keywords:
  - afterMessageId
  - AgentTurnGroup
  - agentActivityBlocks
  - apply_production_fields
  - 9：16
  - 已完成本轮处理
---

## 结论摘要

1. **顺序错乱**：`nativeTurnGroups` 挂在全部消息末尾，补字段用户气泡插在中间，
   Agent 上一轮回复却沉到最下面。改为 `video_agent_v2` 下把 `AgentTurnGroup`
   写入 `agentActivityBlocks`，用 `resolveThinkingAfterMessageId` 锚到触发该 Turn 的用户消息。

2. **补字段空转**：短跟进「1. 9：16 2.不用引导」未落库，模型空回「已完成本轮处理」。
   在 `native_invoke` 增加 `_bootstrap_production_fields_if_needed`：识别补字段门闩后
   LLM 分析 → 写入 script/form_values → 发 tool 事件 → fallback 公开回答。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/ChatPanel.tsx`
- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/production_fields.py`

## 核心逻辑

1. `startTurn` 已写 `thinkingTurnAnchorsRef[runId]=clientInputId`，锚点可复用。
2. 补字段优先于成稿 bootstrap；已有 script + awaiting/missing 才触发。
3. `choose_public_response_text(..., fallback=)` 避免默认「已完成本轮处理」。

## 注意事项

- 无锚点 Turn 仍可走 `nativeTurnGroups` 末尾兜底。
- 「不用引导」应映射 `ending_cta=none`（由生产字段 LLM 负责）。
