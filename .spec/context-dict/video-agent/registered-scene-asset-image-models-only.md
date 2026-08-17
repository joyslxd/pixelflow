---
topic: 生图模型推荐只能来自已注册 Borgrise
module: video-agent
date: 2026-08-14
keywords:
  - generate_scene_assets
  - registered_scene_asset_image_models
  - gpt-image-2
  - seeddream-5.0
  - Midjourney
  - image-2
---

## 结论摘要

引导选生图模型时，Agent 只能推荐当前已注册的 Borgrise 入口：`gpt-image-2`（展示 image-2）、`seeddream-5.0`（展示 Seedream 5.0）。禁止推荐 Midjourney / DALL·E / Stable Diffusion 等未注册模型。

## 关键文件

- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/workspace/digest.py`（`REGISTERED_SCENE_ASSET_IMAGE_MODELS`）
- `backend/pixelflow/video_agent/tools/scene_packages.py`（`generate_scene_assets` description）
- `web/src/lib/sceneAssetModelSelection.ts`

## 核心逻辑

1. Workspace digest 注入 `registered_scene_asset_image_models`
2. 系统提示要求推荐时只读该列表
3. FE 模型卡与 digest 同源两模型

## 注意事项

- 以后加/换生图模型：同步改 digest 常量、FE `SCENE_ASSET_PREFERRED_MODELS`、Tool description
- 不要让模型按「电影感」泛化推荐平台外模型
