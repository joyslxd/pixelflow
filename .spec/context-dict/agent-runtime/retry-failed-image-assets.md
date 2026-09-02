---
topic: 失败图片资产重试 Tool
module: agent-runtime
date: 2026-09-01
keywords:
  - retry_failed_image_assets
  - generate_image_assets
  - failed
  - planned
---
## 结论摘要

`retry_failed_image_assets` 是非计费恢复 Tool：只把 `origin=planned_generation` 且 `failed/timeout/expired` 的资产改回 `planned`，保留 `asset_id` 与 `generation_prompt`，清掉失败字段和旧图引用。真正出图仍必须再走确认后的 `generate_image_assets`。ready 素材和用户上传素材不能用它重置。

## 关键文件

- `backend/pixelflow/agent_tools/video/image_asset_retry.py`
- `backend/pixelflow/agent_tools/catalog.py`
- `backend/skills/skills/image-generation/SKILL.md`
- `backend/tests/test_retry_failed_image_assets.py`

## 核心逻辑

1. inspect 发现 failed → `retry_failed_image_assets` → `generate_image_assets`。
2. 已是 planned 的资产幂等保留，不报错。
3. 禁止用 `prepare_scene_packages` 覆盖已有 ID 或新建 ID 来绕过失败态。

## 注意事项

- 本 Tool 不计费、不调 Provider；看板若仍失败，可能是后续 generate 未确认或 Provider 再次失败。
