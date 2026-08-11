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
---

## 结论摘要

第二轮「9:16,结尾不需要引导」曾因整篇脚本再跑生产字段 LLM 超时 fail-closed，Planner 又执行 `import_script` 变成版本 2。现改为：跟进只分析【本轮指令】摘要；识别「结尾不需要引导」；跟进后只落 inspect + Plan 卡；禁止再 import。优先由思考流裁决；思考无 `entry_path` 时再走 `analyze_production_fields_with_llm` 降级。

字段齐备后文案引导「右侧脚本预览底部确认」。对话框「已更新脚本版本 N」可点开右侧脚本草稿。V2 **不再**静默隐藏补字段 Plan。

## 相关文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/thinking_stream.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/components/chat/MessageBubble.tsx`

## 核心逻辑

1. `build_production_fields_excerpt`：有本轮指令时优先短输入
2. 思考流 `inspect` / 缺字段 → inspect；无裁决时 field_followup → 更新 missing → inspect（public_goal=通知文案）
3. `splitScriptVersionPreviewParts` + `onOpenScriptPreview` 打开脚本预览
4. Path B 导入稿用时间码（`0—10秒`）也可 `workspaceHasExportReady`

## 注意事项

日志里 `LLM 生产字段分析失败 error_type=TimeoutError` 即本问题信号；补字段回合不应再出现「已导入脚本版本 N」。
Path B 字段齐备后不要写「可点确认卡」——该路径没有 `confirm_script_creative` 闸门。
入场顺序是先思考后规划，不要再推并行「规划中」空卡。
