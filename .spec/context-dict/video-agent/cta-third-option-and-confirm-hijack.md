---
topic: 「第三个」CTA 与确认脚本被补字段截胡
module: video-agent
date: 2026-08-13
keywords:
  - ending_cta
  - 第三个
  - 留白收束
  - apply_production_fields
  - 确认脚本
  - prepare_scene_packages
  - enrich_analysis_with_choice_replies
---

## 结论摘要

用户回复「1. 9：16 2. 第三个」后仍被追问「结尾行动引导」，常见是 LLM 没把「第三个」映射到追问菜单的③留白收束（`ending_cta=none`）。随后点「确认脚本」时，补字段门闩因 `awaiting/missing` 仍为真，把确认 Turn 截胡成再次「补全生产字段」；模型还可能继续误调 `prepare_scene_packages`，进度卡卡在「调用场景包生成 Skill」。

修复：1) 标准多选序号 enrich（③→none）；2) 「确认脚本」不进补字段门闩；3) 补字段 bootstrap 后短接，不再进模型；4) 确认时若仍缺字段只提示、不 prepare。

## 相关文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/tests/test_video_agent_production_fields.py`
- `backend/tests/test_video_agent_native_invoke.py`

## 核心逻辑

1. `enrich_analysis_with_choice_replies`：仅补 LLM 仍缺的项；「第三个/留白」→ none
2. `looks_like_production_field_reply`：确认短令直接 False
3. `apply_production_fields` bootstrap 有公开回复则 `_emit_response_completed` 后返回
4. confirm bootstrap：`reconcile_missing_with_workspace` 非空则拒 prepare

## 注意事项

- 「把第三个分镜改掉」不得当成 CTA 点选
- ④自定义无文案仍缺项
- 按钮确认已改走 `commands/confirm-script-plan`，不再靠「确认脚本」话术 bootstrap prepare
- 已卡住的 prepare 进度卡需刷新或重新点确认；根因曾是确认/补字段竞态
