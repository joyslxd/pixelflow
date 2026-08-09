---
topic: 脚本步骤阶段性进度展示
module: video-agent
date: 2026-08-07
keywords:
  - agent.step.progressed
  - brainstorm_script
  - brief_generate
  - progressLog
---
## 结论摘要
长耗时 `brainstorm_script` 不能只显示转圈。执行器通过 `VideoToolContext.emit_progress` 调用 `progress_step_with_event`，写入 `agent.step.progressed`（含 `public_summary` + `progress_phase`）。脚本工具阶段：准备输入 → 调用 brief_generate Skill → 等待大模型 → 整理草稿。前端时间线用 `progressLog` 展示已完成/当前阶段。

## 关键文件
- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/executor/service.py`
- `backend/pixelflow/video_agent/executor/events.py`
- `backend/pixelflow/video_agent/workspace/repository.py`
- `web/src/features/video-agent/AgentPlanTimeline.tsx`
- `web/src/features/video-agent/state/reducer.ts`

## 核心逻辑
1. Executor 给工具上下文注入 `report_progress` → Memory/SQL `progress_step_with_event`。
2. progressed 事件 ID 按 `progressed:{phase}` 幂等，同一阶段重放不重复。
3. Reducer 对 `agent.step.progressed` 追加 `progressLog`，保持 `status=running`。

## 注意事项
- 阶段文案只允许公开安全描述，禁止 prompt / 供应商原始错误。
- 等待大模型阶段可能仍占大部分时长；UI 至少要停在「已交给大模型…」。
- 其它长工具（参考视频拆解、镜头生成）可复用同一 `emit_progress` 钩子。
