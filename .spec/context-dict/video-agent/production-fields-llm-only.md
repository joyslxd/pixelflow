---
topic: 生产字段一律 LLM 判定
module: video-agent
date: 2026-08-11
keywords:
  - analyze_production_fields_with_llm
  - 9：16
  - missing_requirements
  - 禁止正则
---

## 结论摘要

画幅 / CTA / 总时长缺项判定必须走 `analyze_production_fields_with_llm`，禁止本地正则猜字段。短补丁「9：16」只做全角冒号归一，并凭 workspace 已有脚本/缺项合并上下文后进 Planner；补丁后用 LLM 刷新 `script.missing_requirements`。

## 关键文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/executor/service.py`

## 核心逻辑

1. import / confirm / 补字段跟进：LLM 输出 duration_sec + missing（仅画幅/CTA）
2. `looks_like_production_field_reply`：看短回复 + workspace 缺项/脚本，不解析字段内容
3. 确认卡 cost_summary：读 workspace 已落库的 missing/duration

## 注意事项

全角归一不得作用于完整脚本正文；「继续生成视频」不得被当成补字段 followup。
