---
topic: 对话改图用 revise_image_assets 重置 ready
module: agent-runtime
date: 2026-09-07
keywords:
  - revise_image_assets
  - generate_image_assets
  - retry_failed_image_assets
  - ready
  - generation_prompt
---
## 结论摘要

用户在对话里点名修改已就绪参考图时，必须先走非计费 `revise_image_assets`：更新 `generation_prompt`（可选），把 `ready/failed` 重置为 `planned`，保留 `asset_id` 与分镜引用。真正出图仍由确认后的 `generate_image_assets` 扣费；不要在对话里再要「确认重生成」。上传素材和正在生成的资产一律拒绝。

## 关键文件

- `backend/pixelflow/agent_tools/video/image_asset_revise.py`
- `backend/pixelflow/agent_tools/catalog.py`
- `backend/skills/skills/image-generation/SKILL.md`
- `backend/skills/skills/pixelflow-video-orchestration/SKILL.md`
- `backend/tests/test_revise_image_assets.py`

## 核心逻辑

1. 入参是点名列表 `assets[{asset_id, generation_prompt?}]`，禁止整表重排。
2. `origin` 必须是 `planned_generation`；`generating` 与 `existing_material` 拒绝。
3. `ready/failed/timeout/expired` 清掉旧图与失败投影后改回 `planned`。
4. `generate_image_assets` 仍只收 `planned`，并强制确认卡。

## 注意事项

- `retry_failed_image_assets` 仍只服务失败且不改提示词的恢复，不要删。
- 不要新建 `asset_id`，也不要用 `prepare_scene_packages` 覆盖已有资产。
