---
topic: 生图正确链路与失败资产重试
module: agent-runtime
date: 2026-09-01
keywords:
  - generate_image_assets
  - inspect_image_assets
  - retry_failed_image_assets
  - prepare_scene_packages
  - failed
  - planned
  - asset_updates
---
## 结论摘要

`generate_image_assets` 只接受 `origin=planned_generation` 且 `state=planned` 的资产。`failed` / `ready` 都会被拒绝。`prepare_scene_packages` 的 `asset_registry` 不能覆盖已有 `asset_id`；`asset_updates` 只改已上传素材的 slot/kind/role，不能把 failed 改回 planned。失败图的合法重试入口是非计费 Tool `retry_failed_image_assets`：保留原 `asset_id` 与 `generation_prompt`，清掉失败投影后再走 `generate_image_assets`。

## 关键文件

- `backend/pixelflow/agent_tools/video/image_assets.py`
- `backend/pixelflow/agent_tools/video/image_asset_retry.py`
- `backend/pixelflow/agent_tools/video/storyboard.py`
- `backend/pixelflow/generation_jobs/projector.py`
- `backend/skills/skills/image-generation/SKILL.md`

## 核心逻辑

1. Provider 失败后 projector 把资产写成 `state=failed`，并保留 `generation_prompt`。
2. 再次 `generate_image_assets` 因 state≠planned 直接校验失败；`attempt` 只用于 Job 幂等，不重置 Workspace 状态。
3. 用原 ID 再写 `asset_registry` 会报「只能登记新的待生成资产」。
4. `asset_updates` 目标必须是 `origin=existing_material`，对 planned_generation 无效。
5. 正确顺序：`inspect_image_assets` → `retry_failed_image_assets` → `generate_image_assets`。

## 注意事项

- 界面「生成任务失败」是 Run 终态，不一定是 Provider 又失败了一次；常见是校验拒绝后 Agent 循环 prepare 超限。
- 不要新建 asset_id、不要改分镜引用、不要用 prepare 覆盖已有 ID。
