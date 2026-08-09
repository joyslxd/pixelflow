---
topic: 执行方案与消息锚点
module: video-agent
date: 2026-08-09
keywords:
  - videoAgentPlanAnchors
  - video_agent_plan_anchors
  - agent.plan.created
  - AgentPlanTimeline
---
## 结论摘要
新一轮用户请求会创建新的 `agent.plan.created`。应累积 `videoAgentPlans`/`videoAgentPlanOrder`，并把每个 plan 锚到触发它的用户消息之后。方案正文来自 Snapshot `plans`（DB）；锚点写入会话 context（`video_agent_plan_anchors`），sessionStorage 仅热缓存。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/lib/supervisor/workspaceProjection.ts`
- `web/src/features/video-agent/planHistory.ts`
- `.spec/context-dict/video-agent/plan-history-server-persist.md`

## 核心逻辑
1. BE：`list_conversation_plans` → Snapshot `videoAgent.plans`
2. FE：`videoAgentPlanAnchors[planId] = userMessageId`，debounce 写入 conversation context
3. 锚点消息 id 失效时回落到仍存在的用户消息

## 注意事项
- Snapshot hydrate 合并历史 plans，不以单槽 `videoAgentPlan` 覆盖旧方案
- 恢复会话期间禁止用热缓存回写覆盖服务端锚点
