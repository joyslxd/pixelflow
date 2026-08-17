---
topic: 空 target_assets 导致参考图秒失败
module: video-agent
date: 2026-08-13
keywords:
  - target_assets
  - scene_asset_jobs_empty
  - 没有可生成的参考图素材
  - generate_scene_assets
  - GenerateSceneAssetsInput
---
## 结论摘要
选模型后 `generate_scene_assets` 耗时 0 秒报「没有可生成的参考图素材」，即使场景包卡片已有角色/场景/道具。根因：Tool 默认 `target_assets=()` → Operation 传入 `[]`；领域层把「非 None」当成失败重试过滤，空列表滤掉全部作业后触发 `scene_asset_jobs_empty`。修复：空列表视为全量生成（与 `None` 同义）；仅非空列表才按目标过滤。

## 关键文件
- `backend/pixelflow/generate/scene_assets.py`（`if target_assets:`）
- `backend/pixelflow/video_agent/adapters/domain_jobs.py`（空 list → `None`）
- `backend/pixelflow/video_agent/tools/scene_packages.py`（`GenerateSceneAssetsInput.target_assets` 默认 `()`）
- `backend/tests/test_scene_assets.py`

## 核心逻辑
1. 全量生图：`target_assets is None` 或 `[]`
2. 失败重试：非空 `[{asset_id, asset_type}, ...]`
3. 真无资产（global_assets 空且分镜无角色/场景/道具）才应 `scene_asset_jobs_empty`

## 注意事项
- 卡片「可绑定资产」来自投影的 `global_assets`，与本 bug 可并存：有资产仍 0 作业
- 改默认字段类型时勿再把空容器当「显式过滤」
