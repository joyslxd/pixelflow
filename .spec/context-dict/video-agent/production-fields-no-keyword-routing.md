---
topic: production_fields 边界与禁止话术硬匹配
module: video-agent
date: 2026-08-12
keywords:
  - production_fields
  - looks_like_production_field_reply
  - looks_like_scene_asset_continue
  - target_capability
  - 硬编码
---

## 结论摘要

`production_fields.py` 只负责：全角标点归一、LLM 抽取时长/画幅/CTA、把结果与工作区已落库事实合并、拼公开追问文案。  
**禁止**在此文件用关键词判断「用户是不是要生图/成片/补字段」。曾加的 `looks_like_scene_asset_continue` 等已删除。

补字段降级门闩 `looks_like_production_field_reply` 只认结构：短文本 +（`awaiting_production_fields` 或 script.missing）。  
参考图推进靠工作区事实闸门（有 scene_packages、无参考图 URL）+ Intake `target_capability`/`intent`，不是话术列表。

## 相关文件

- `backend/pixelflow/video_agent/production_fields.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/thinking_stream.py`（Intake 裁决）

## 核心逻辑

1. 字段有无 → `analyze_production_fields_with_llm`
2. 是否进补字段降级 → 结构门闩 + Intake 未裁定生图/成片 + 非「场景包待生图」阶段
3. 是否生成参考图 → `has_scene_packages && !has_scene_asset_images` + Intake 非成片意图

## 注意事项

- Entrypoint 里 `_is_continue_video_generation` 仍有历史 marker，属另一处降级，后续应收口到 Intake
- 「有脚本就当补字段」旧逻辑已废，避免任意短句误伤
