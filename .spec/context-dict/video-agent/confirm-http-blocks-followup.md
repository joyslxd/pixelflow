---
topic: 同意创意确认 HTTP 被后续步骤拖死导致卡住
module: video-agent
date: 2026-08-10
keywords:
  - 同意创意继续
  - confirm_step
  - stop_after_step_id
  - AWAITING_CONFIRMATION
  - Post-Confirm Continue
  - confirmation 500
---
## 结论摘要
「同意创意继续」后 UI 仍停在「待确认」，根因是 `confirm_step` 在确认 HTTP 里同步跑完后续全部步骤（含 `/plan`/`/characters` 长 LLM）。请求超时/刷新中断后确认接口 500，前端状态仍像未确认。修复：确认 HTTP 只跑完确认步（`stop_after_step_id`），有后续 PENDING 时后台 `resume_plan`。

## 关键文件
- `backend/pixelflow/video_agent/executor/service.py`（`confirm_step` + `_continue(stop_after_step_id)`）
- `backend/pixelflow/agent_runtime/service.py`（确认成功后 `_schedule_executor_notification(resume_plan)`）
- `backend/tests/test_video_agent_executor.py` / `test_video_agent_confirmation_api.py`

## 核心逻辑
1. `confirm_step` → `_continue(..., stop_after_step_id=确认步)`，完成后立即返回（plan 常为 RUNNING）
2. `respond_to_video_agent_confirmation`：确认步 COMPLETED 且仍有 PENDING → 异步 `resume_plan(credential=None)`
3. 确认步仍 RUNNING（如挂了 pending operation）时不调度后台续跑
4. 计费确认单步计划：无后续 PENDING，行为与原来一致（HTTP 内完成并 COMPLETED）

## 注意事项
- 后台续跑无用户 Authorization；脚本 Skill LLM 阶段不依赖 credential，后续计费步仍会再走确认闸门
- 历史卡住会话：刷新后依赖陈旧 RUNNING 恢复，或重新点同意 / 新开一轮
- 勿再在确认 HTTP 路径里同步执行长 LLM
