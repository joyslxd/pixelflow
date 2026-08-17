---
topic: turns/start 500 因 frontend_v2 升级 revision 冲突
module: video-agent
date: 2026-08-13
keywords:
  - turns/start
  - 500
  - AgentRuntimeRecordConflictError
  - FrontendV2LegacyUpgrader
  - revision
  - 409
---

## 结论摘要

新建会话默认 `orchestration_mode=frontend_v2`。首条视频 Turn 会在 `start_turn` 内升级为 `video_agent_v2`。前端常在同一时刻 `PUT` 会话标题/context，升级 CAS 撞 revision → `AgentRuntimeRecordConflictError` 未被路由捕获，表现为 **HTTP 500 且访问日志无堆栈**（HTTPException 路径外的裸 RuntimeError 偶发未落 Traceback，或被并发请求淹没）。

修复：升级冲突时重读会话再试一次；仍失败则路由映射 **409** `agent_runtime_record_conflict`；前端 startTurn 遇 409 刷新 Snapshot 后重试一次。

## 相关文件

- `backend/pixelflow/agent_runtime/service.py`（upgrade 重试）
- `backend/app/gateway/routers/pixelflow_conversations.py`（409 映射）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（startTurn 409 重试）

## 核心逻辑

1. 新建对话 assignment 固定 FRONTEND_V2，等首 Turn 路由
2. wants_video_agent + frontend_v2 → `FrontendV2LegacyUpgrader.upgrade_if_needed`
3. 冲突 → reload；若已是 video_agent_v2 则继续登记 Turn

## 注意事项

- 旧脏 500 对话可直接再发一条；不必删会话
- 若仍 409，检查是否有其他并发写 conversation revision 的请求
