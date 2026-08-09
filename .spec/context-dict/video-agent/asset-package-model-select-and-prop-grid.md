---
topic: 资产包模型选择卡与道具四宫格提示词
module: video-agent
date: 2026-08-09
keywords:
  - generate_images
  - awaiting_image_model
  - scene_asset_model_options
  - gpt-image-2
  - seeddream-5.0
  - PROP_MULTI_SCENE_GRID_PROMPT_SUFFIX
  - generate-scene-assets
---
## 结论摘要
Video Agent 资产包改为「先结构 → 选生图模型 → 再生图」。`prepare-scene-packages` 传 `generate_images=false` 时 Job 以 `status=completed`、`stage=awaiting_image_model` 结束并弹出 `scene_asset_model_options` 卡；确认后写入 `creation_contract.image_model`（gpt→4K / seeddream→2K）并启动独立 `generate-scene-assets` Job。道具参考图仍单张，仅在 prompt 末尾追加四宫格多场景后缀。

## 关键文件
- `backend/app/gateway/routers/pixelflow_video.py`（`PrepareScenePackagesRequest.generate_images`）
- `backend/pixelflow/generate/scene_assets.py`（`enhance_prop_multi_scene_grid_prompt`）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`startVideoAgentAssetPackageFromScript` / `handleConfirmSceneAssetModel`）
- `web/src/components/chat/MessageBubble.tsx`（模型选择卡）
- `web/src/lib/sceneAssetModelSelection.ts`
- `web/src/features/video-agent/AgentPipelineProgress.tsx`（`awaiting_image_model` 步骤）

## 核心逻辑
1. prepare 结构 ok 且 `generate_images=false` → 不调用 `_generate_scene_assets_response`
2. FE 收到 `awaiting_image_model` → 结构卡（`sceneAssetsAwaitingModel=true`）+ 模型卡
3. 确认模型 → `api.startSceneAssetsJob`，pending kind=`scene_asset_generation`，继续进度 tip / 终态卡
4. 道具 queue 时 `enhance_prop_multi_scene_grid_prompt(...)`；角色/场景不加

## 注意事项
- 默认 `generate_images=True`，旧调用方行为不变
- 模型展示名：`gpt-image-2`→image-2，`seeddream-5.0`→Seedream 5.0
- 确认成片在 `sceneAssetsAwaitingModel` / `sceneAssetsGenerating` 时禁用
