---
topic: 补生产字段跟进必须带上下文
module: video-agent
date: 2026-08-11
keywords:
  - production fields
  - 画幅
  - CTA
  - 结尾不变
  - awaiting_confirmation
  - stream_intake_thinking
  - merge context
  - waiting_for_input
  - Planner
---

## 结论摘要

用户在创意确认闸门期间回复「180s 9:16 结尾不变」时：必须先合并 workspace 脚本/brief 成 `【本轮指令】`，再思考流，再落计划。禁止本地成稿思考文案；禁止被「待确认」闸门吞成无上下文 inspect；已有脚本时禁止再走 polish 重导入。

P1 后：补字段已齐 → **fallthrough Planner**（不再 inspect-only 短路）；仍缺 → `WAITING_FOR_INPUT` 空 steps Plan。`entry_path=inspect` 只写事实后同样交 Planner。

## 相关文件

- `backend/pixelflow/video_agent/entrypoint.py`（`_merge_turn_with_workspace_context`、确认闸门放行、field_followup、waiting Plan）
- `backend/pixelflow/video_agent/thinking_stream.py`（一律 LLM；截断保留本轮指令）
- `backend/pixelflow/video_agent/production_fields.py`（`结尾不变` / `180s`）
- `.spec/context-dict/video-agent/waiting-for-input-plan.md`

## 核心逻辑

1. `submit_turn`：merge → thinking → `_submit_turn_after_thinking`
2. 闸门：`looks_like_production_field_reply(本轮指令)` 允许继续，不被待确认吞掉
3. 思考截断：有 `【本轮指令】` 时保留尾部，避免只看见脚本头、丢补字段
4. field_followup：`analyze_production_fields_with_llm` → missing 则 waiting，否则 Planner

## 注意事项

- 「结尾不变」视为 CTA 已确认，不再追问行动引导
- 补字段齐后 `planner.calls` 应增加；不应出现「已导入脚本版本 N」的重复 polish
- 首轮缺字段 `planner.calls == 0` 且 Plan 为 `waiting_for_input`
