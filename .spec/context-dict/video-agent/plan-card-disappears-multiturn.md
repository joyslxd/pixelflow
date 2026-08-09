---
topic: 执行方案卡片多轮消失原因
module: video-agent
date: 2026-08-09
keywords:
  - AgentPlanTimeline
  - agentActivityBlocks
  - list_conversation_plans
  - agent-snapshot.plans
---
## 结论摘要
「执行方案 · …」不是聊天消息，而是挂在用户消息后的 `agentActivityBlocks`。后端曾只在 Snapshot 返回最新 plan；多轮后前端内存丢失旧 plan，卡片就消失。现权威来源改为 `agent-snapshot.plans`（DB `list_conversation_plans`）；sessionStorage 仅热缓存。

## 关键文件
- `backend/pixelflow/video_agent/workspace/repository.py`
- `backend/pixelflow/agent_runtime/service.py`
- `web/src/lib/supervisor/workspaceProjection.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `.spec/context-dict/video-agent/plan-history-server-persist.md`

## 核心逻辑
1. DB 已有多 plan；Snapshot 按 created_at 升序返回 `plans[]`
2. FE 投影到 `videoAgentPlans` / `videoAgentPlanOrder`，按锚点插回对话流
3. 锚点写入会话 `context.video_agent_plan_anchors`

## 注意事项
- 勿再把 sessionStorage 当权威存储
- 换浏览器/清缓存后仍应能从 Snapshot + conversation context 恢复
