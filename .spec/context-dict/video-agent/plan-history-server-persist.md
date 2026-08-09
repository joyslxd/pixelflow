---
topic: 执行方案历史服务端持久化
module: video-agent
date: 2026-08-09
keywords:
  - list_conversation_plans
  - agent-snapshot.plans
  - videoAgentPlanAnchors
  - sessionStorage
---
## 结论摘要
执行方案已写入 `pixelflow_video_agent_plans` 表，但 Snapshot 以前只返回最新 plan，前端只能靠 sessionStorage 记历史。现改为 `list_conversation_plans` + Snapshot `videoAgent.plans` 带回全部方案；锚点写入会话 `context.video_agent_plan_anchors`。sessionStorage 仅热缓存。

## 关键文件
- `backend/pixelflow/video_agent/workspace/repository.py`
- `backend/pixelflow/agent_runtime/service.py`
- `web/src/lib/supervisor/workspaceProjection.ts`
- `web/src/lib/supervisor/reducer.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑
1. DB 已有多 plan；Snapshot 按 created_at 升序返回 `plans[]`
2. FE 投影填入 `videoAgentPlans` / `videoAgentPlanOrder`
3. 锚点随 conversation context 落库，换设备可恢复

## 注意事项
- 旧客户端忽略 `plans` 字段仍可用 `plan+steps`
- 勿再把 sessionStorage 当权威存储
