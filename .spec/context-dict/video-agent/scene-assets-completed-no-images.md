---
topic: 参考图步骤完成但对话无图
module: video-agent
date: 2026-08-12
keywords:
  - 参考图生成完成
  - 0秒
  - scene_asset_jobs_empty
  - countGlobalAssetImageUrls
  - gpt-image-2
  - 生图模型确认
---

## 结论摘要

时间线「生成参考图 / 参考图生成完成 / 0秒」**只表示 Plan 步骤终态**，不等于聊天里已有带图结果卡。常见假象：
1. 工作区结构卡已投影后，`global_assets` 有对象就 early-return，参考图 URL 写入后卡片不刷新；
2. `generate_scene_assets` 在无可排队素材时曾 `ok=True` 空跑，公开文案仍是「参考图生成完成」；
3. 生图模型选择按钮已下线，确认卡未写明默认模型，用户感觉「没选模型却完成」。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/generate/scene_assets.py`
- `backend/pixelflow/video_agent/executor/service.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/video_agent/entrypoint.py`

## 核心逻辑

1. 投影卡比较 `countGlobalAssetImageUrls`；图数变化必须 upsert
2. `total==0` → `ok=False` + `scene_asset_jobs_empty`
3. 默认模型 `seeddream-5.0`；确认卡写明模型/画幅/清晰度

## 注意事项

- 真生成通常远大于 0 秒；0 秒完成优先怀疑空作业或未落图
- 打开「视频场景包」卡片看角色/场景/道具图；不要只看 Plan 时间线
