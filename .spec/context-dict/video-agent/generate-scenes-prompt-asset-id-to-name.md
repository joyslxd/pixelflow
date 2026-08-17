---
topic: generate-scenes Provider 提示词把 @asset_id 换成正式名
module: video-agent
date: 2026-08-17
keywords:
  - generate_scenes
  - @asset_id
  - @正式名称
  - reference_mode_video
  - build_scene_provider_request
  - 素材对不上
---

## 结论摘要

Workspace / 分镜正文故意存 `@character-1` 这类稳定 `asset_id`（便于 mentions 绑定与 FE 回写）。发给 content-app `reference-mode-video` 的 `prompt` 若仍带不透明 ID，视频模型难以把参考图与「安然 / Yann」身份对齐。

优化点：只在 `build_scene_provider_request` 组装 Provider 请求时，把 `@asset_id` 改写为 `@正式名称`；`image_urls` 仍按 `asset_id` 从 `global_assets` / mentions 解析，不改落库正文。

## 关键文件

- `backend/pixelflow/video_agent/adapters/scene_operation.py`（`_rewrite_prompt_asset_ids_to_names`）
- `backend/pixelflow/generate/scene_packages.py`（入库时 `@展示名` → `@asset_id`）
- `backend/tests/test_video_agent_scene_operation.py`

## 核心逻辑

1. 入库：`_ensure_reference_asset_tokens` 把展示名规范化为 `@asset_id`
2. 生成：`_resolve_scene_prompt` 读 `shot_description.text`（仍是 asset_id）
3. 出站：`_rewrite_prompt_asset_ids_to_names` 用 `global_assets` + mentions 的 `name` 替换后再 POST

## 注意事项

- 不要为了「好看」把 Workspace 正文改回展示名，否则 patch / mentions / 脏镜头集合会漂
- 名称含 `@` 的资产跳过改写，避免提示词语法破坏
- 改写正则必须用 ASCII `[A-Za-z0-9_-]`，不能用 `\w`：中文紧跟 ID 时（`@character-2握住`）`\w` 会吞掉汉字导致整段匹配失败
- FE 展示层可继续做 asset_id → 展示名映射，与本改写互补
