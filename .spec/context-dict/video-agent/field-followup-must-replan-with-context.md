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
  - inspect 不 import
---

## 结论摘要

用户在创意确认闸门期间回复「180s 9:16 结尾不变」时：必须先合并 workspace 脚本/brief 成 `【本轮指令】`，再思考流，再落计划。禁止本地成稿思考文案；禁止被「待确认」闸门吞成无上下文 inspect；已有脚本时禁止再走 polish 重导入。

思考流给出 `entry_path=inspect` / 缺字段追问时直接 inspect；思考失败无裁决时，补字段仍走 `analyze_production_fields_with_llm` → inspect（不调 Planner）。

## 相关文件

- `backend/pixelflow/video_agent/entrypoint.py`（`_merge_turn_with_workspace_context`、确认闸门放行、field_followup 降级）
- `backend/pixelflow/video_agent/thinking_stream.py`（一律 LLM；截断保留本轮指令）
- `backend/pixelflow/video_agent/production_fields.py`（`结尾不变` / `180s`）

## 核心逻辑

1. `submit_turn`：merge → thinking → `_submit_turn_after_thinking`
2. 闸门：`looks_like_production_field_reply(本轮指令)` 允许继续，不被待确认吞掉
3. 思考截断：有 `【本轮指令】` 时保留尾部，避免只看见脚本头、丢补字段

## 注意事项

- 「结尾不变」视为 CTA 已确认，不再追问行动引导
- 补字段回合 `planner.calls` 不得增加；不应出现「已导入脚本版本 N」
