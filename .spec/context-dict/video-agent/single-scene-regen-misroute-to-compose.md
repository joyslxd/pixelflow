---
topic: 单镜重生成被误路由到合并成片
module: video-agent
date: 2026-08-17
keywords:
  - 确认并生成分镜视频
  - generate_scenes
  - compose_or_export_video
  - awaiting_production_fields
  - tool_commitment
---

## 结论摘要

用户改完 scene-1 后点「确认并生成分镜视频（scene-1）」，Agent 却长思考并走向
全量 14 镜确认 / 合并成片。根因组合：1) `awaiting_production_fields` 时补字段门闩
可能截胡该短令；2) ReAct 口述「成片/合并」被 commitment 强制补 `compose_or_export_video`；
3) 提示词未强调括号内 scene_id 只生成该镜。

## 关键文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/middleware/tool_commitment.py`
- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/pixelflow/video_agent/native_invoke.py`（既有 bootstrap）

## 核心逻辑

1. 生成分镜短令不得进补字段 / 确认脚本门闩。
2. bootstrap `generate_scenes` 对括号 scene_id 只生成该镜并短接。
3. Gateway：latest_input 为生成分镜意图时拒绝 compose。
4. commitment：用户在生成分镜时禁止强制 compose。

## 注意事项

- 「确认并生成视频」仍是确认脚本类；「确认并生成分镜视频」是 generate_scenes。
- 合并只能在用户明确说合并/合成/导出时调用。
