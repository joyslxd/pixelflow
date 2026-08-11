---
topic: 确认脚本后取消卡住的润色执行卡
module: video-agent
date: 2026-08-09
keywords:
  - cancel_active_script_skill_plans
  - script_plan_confirmed
  - compliance
  - 成稿自检与导出
  - SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS
  - 假忙碌
---
## 结论摘要
路径 B 的「执行方案 · 成稿自检与导出」与资产包/成片是并行链路。用户成稿本身可满足 `exportReady`，确认脚本后即可生图成片，但合规 LLM 若挂起，执行卡会一直显示「正在执行」。修复：`confirm_for_generation` 与已确认会话的 snapshot 会 `cancel_active_script_skill_plans`；脚本 Skill 单阶段 LLM 超时 180s；executor 在 plan 已取消时不再 complete 被跳过的步骤。

## 关键文件
- `backend/pixelflow/video_agent/workspace/repository.py`（`cancel_active_script_skill_plans`）
- `backend/pixelflow/agent_runtime/service.py`（确认脚本 / snapshot 收口；CANCELLED 也释放 Turn）
- `backend/pixelflow/video_agent/executor/service.py`（取消后不写 complete）
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`（阶段超时）

## 核心逻辑
1. 识别活跃脚本 Skill 计划：状态为 planning/running/awaiting_confirmation，且步骤工具均为 `run_script_skill_stage`
2. 未完成步骤 → SKIPPED（摘要：用户已确认脚本并开始生成资产包），计划 → CANCELLED
3. 已确认会话刷新 Snapshot 时同样收口，修复历史卡住会话
4. `_generate_stage_markdown` 用 `asyncio.wait_for`；超时返回可完成摘要，避免永久 RUNNING

## 注意事项
- 非脚本计划（如 `generate_scenes`）不会被取消
- 取消后仍在飞的 LLM 返回时，executor 直接返回 CANCELLED plan，不复活步骤
- Turn 终态收口已包含 `CANCELLED`
