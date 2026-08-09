---
topic: VideoAgent Turn 未收尾导致假排队
module: video-agent
date: 2026-08-08
keywords:
  - 输入已排队
  - TurnStatus.QUEUED
  - complete_turn_and_claim_next
  - ACCEPTED
  - runtimeNotice
---
## 结论摘要
「已排队 1 条 · 第 1 位」通常不是真有多条需求，而是上一轮 VideoAgent Plan 跑完后 **Agent Runtime Turn 仍停在 ACCEPTED/PROCESSING**。登记新 Turn 时发现 `execution_owner_count > 0`，就把本轮标成 QUEUED。前端 `resolveSupervisorRuntimeNotice` 只要 inputQueue 里有 `queued` 就弹出提示。

## 关键文件
- `backend/pixelflow/agent_runtime/persistence/turn_registration.py`（有 ACCEPTED/PROCESSING 则新 Turn→QUEUED）
- `backend/pixelflow/agent_runtime/service.py`（`_release_stale_completed_video_agent_turns` / `_finalize_video_agent_turn`）
- `web/src/lib/supervisor/runtimeNotice.ts`

## 核心逻辑
1. `start_turn` 登记前先收尾「Plan 已 COMPLETED/FAILED 但 Turn 未完成」的僵尸占用。
2. `notify_registered_turn` 跑完 Runner 后，Plan 终态则 `complete_turn_and_claim_next`，并推进下一排队 Turn。
3. 文案：若仍有 accepted/processing 占用，提示「上一条任务还在执行…」，避免误解成系统无故排队。

## 注意事项
- 登记前收尾僵尸 Turn 时必须 `chain_next=False`，否则会提前把旧 QUEUED 提成 PROCESSING，新输入又被挤去排队。
- AWAITING_CONFIRMATION / RUNNING（异步 Job）不要过早 complete Turn。
- 链式唤醒下一 Turn 时 credential 可能为空，仅依赖服务端侧能力。
