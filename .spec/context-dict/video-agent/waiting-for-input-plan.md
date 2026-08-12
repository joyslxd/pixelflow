---
topic: WAITING_FOR_INPUT Plan（缺字段等待补充）
module: video-agent
date: 2026-08-11
keywords:
  - waiting_for_input
  - WAITING_FOR_INPUT
  - needs_user_reply
  - missing_requirements
  - cancel_waiting_for_input_plans
  - empty steps
  - AgentPlanTimeline
---

## 结论摘要

生产字段未齐或思考流 `needs_user_reply` 时，Entrypoint 持久化 `status=waiting_for_input`、**空 steps** 的 Plan，不调 Planner、不进 Executor RUNNING。用户补齐后下一轮 Turn 先 `cancel_waiting_for_input_plans`，再走 Planner。Timeline 展示「等待补充」，勿当成「规划中」。

## 相关文件

- `backend/pixelflow/video_agent/contracts/plan.py`（`AgentPlanStatus.WAITING_FOR_INPUT`）
- `backend/pixelflow/video_agent/entrypoint.py`（`_persist_waiting_for_input_plan`）
- `backend/pixelflow/video_agent/workspace/repository.py`（空 steps 仅 waiting 合法；取消旧 waiting）
- `backend/pixelflow/video_agent/executor/service.py`（waiting 早退）
- `backend/pixelflow/agent_runtime/service.py`（waiting 视为 Turn 可收尾，避免队列卡住）
- `web/src/features/video-agent/AgentPlanTimeline.tsx` / `state/contracts.ts` / `state/reducer.ts`

## 核心逻辑

1. 思考流缺字段 → patch `awaiting_production_fields` → `_persist_waiting_for_input_plan`
2. 可推进路径开头取消同会话旧 waiting Plan
3. 补字段降级仍缺 → 再落 waiting；已齐 → fallthrough Planner
4. `entry_path=inspect` 只写事实，**不再**短路成 inspect-only Plan

## 注意事项

- `save_plan` 非 waiting 仍禁止空 steps
- 追问话术只在执行方案卡，不写 `thinking-answer` 气泡（见 `waiting-no-duplicate-bubble-script-preview.md`）
- 手工测：成熟脚本缺 9:16/CTA → waiting → 回复补齐 → 右侧脚本预览可确认 → Planner
- P3 Operation 成功后 replan 另批，不在本状态机内
