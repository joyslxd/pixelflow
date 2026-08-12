---
topic: 思考流与方案卡同锚避免沉底错乱
module: video-agent
date: 2026-08-12
keywords:
  - resolveThinkingAfterMessageId
  - thinkingTurnAnchorsRef
  - afterMessageId
  - AgentThinkingStream
  - AgentPlanTimeline
  - lastPlanAnchorUserMessageIdRef
---

## 结论摘要

多轮后 Thought 沉到最新用户消息下、执行方案卡仍挂在首轮/触发轮，看起来「思考应在卡片上方却错乱」。根因是思考用「最新用户消息」锚点，方案卡用「触发该轮用户消息」。现改为 `thinkingTurnAnchorsRef` + pending `runId→clientInputId` 固定锚到本轮用户消息，与方案卡同锚；同锚下 blocks 数组仍先 thinking 后 plan。

## 相关文件

- `web/src/features/video-agent/thinkingAnchor.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/ChatPanel.tsx`（按 afterMessageId 插块）

## 核心逻辑

1. 发送时 `anchors[message.id]=message.id`
2. `turns/start` 后 `anchors[runId]=clientInputId`
3. live/归档/Snapshot 恢复均走 `resolveThinkingAfterMessageId`
4. 已归档 afterMessageId 不因新用户消息漂移

## 注意事项

- waiting 无 thinking-answer 时更依赖 pending/knownAnchor，不能只靠 latest fallback
- 方案卡 render 兜底也改为最近触发用户，不再写死首条用户
