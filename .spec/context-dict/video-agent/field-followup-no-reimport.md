---
topic: 补字段跟进禁止重导入与超时
module: video-agent
date: 2026-08-11
keywords:
  - field followup
  - import 版本 2
  - TimeoutError
  - 结尾不需要引导
  - agent-ack
  - 脚本预览确认
  - waiting_for_input
---

## 结论摘要

第二轮「9:16,结尾不需要引导」曾因整篇脚本再跑生产字段 LLM 超时 fail-closed，Planner 又执行 `import_script` 变成版本 2。现改为：跟进只分析【本轮指令】摘要；识别「结尾不需要引导」；优先由思考流裁决。

P1 后：仍缺 → `WAITING_FOR_INPUT`；已齐 → **Planner**（不是 inspect-only）。禁止无必要的 polish 重导入。字段齐备后文案引导「右侧脚本预览底部确认」。V2 **不再**静默隐藏补字段 Plan。

## 相关文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/thinking_stream.py`
- `.spec/context-dict/video-agent/waiting-for-input-plan.md`
- `web/src/features/video-agent/AgentPlanTimeline.tsx`

## 核心逻辑

1. `build_production_fields_excerpt`：有本轮指令时优先短输入
2. field_followup → `analyze_production_fields_with_llm` → missing? waiting : Planner
3. `splitScriptVersionPreviewParts` + `onOpenScriptPreview` 打开脚本预览

## 注意事项

日志里 `LLM 生产字段分析失败 error_type=TimeoutError` 即本问题信号；补字段回合不应再出现「已导入脚本版本 N」。
Path B 字段齐备后不要写「可点确认卡」——该路径没有 `confirm_script_creative` 闸门。
入场顺序是先思考后规划；waiting 空卡文案是「等待补充」，不是「规划中」。
