---
topic: 没有参考图直接生成误走成片与缺字段
module: video-agent
date: 2026-08-12
keywords:
  - 没有参考图，直接生成
  - generate_scene_assets
  - generate_scenes
  - field_followup
  - looks_like_production_field_reply
---

## 结论摘要

场景包结构就绪后，助手追问参考图，用户回复「没有参考图，直接生成」时：
1. `looks_like_production_field_reply` 因「短回复+有脚本」误判为补画幅/CTA，字段 LLM 只看指令正文又写出「仍缺少画幅/CTA」waiting 卡；
2. Intake/Planner 误判为「直接生成视频」→ `generate_scenes`，跳过角色/场景/道具参考图。

正确路径：排除补字段误判；场景包已有且尚无参考图 URL 时，由 **Planner** 规划 `generate_scene_assets`（入口只 forbid「无包却要生图」）。

## 相关文件

- `backend/pixelflow/video_agent/production_fields.py`（`looks_like_scene_asset_continue`）
- `backend/pixelflow/video_agent/entrypoint.py`（forbid 闸门，不硬编码 Tool）
- `backend/pixelflow/video_agent/planner/workspace_digest.py`（`has_scene_asset_images`）
- `backend/pixelflow/video_agent/planner/model.py` / `thinking_stream.py`

## 核心逻辑

1. 「没有参考图/生成参考图/开始生图」≠ 生产字段补丁
2. `has_scene_packages && !has_scene_asset_images` + 上述话术 → Planner 选 `generate_scene_assets`
3. digest 暴露 `has_scene_asset_images` 供 Planner/Intake 对照

## 注意事项

- 「确认并生成视频」仍是成片路径，与「没有参考图，直接生成」区分
- 参考图 Tool 仍 `confirmation_required=True`（计费闸门）
- 2026-08-12：入口不再直接构造 `generate_scene_assets` 步骤，见 `entrypoint-control-plane-slim.md`