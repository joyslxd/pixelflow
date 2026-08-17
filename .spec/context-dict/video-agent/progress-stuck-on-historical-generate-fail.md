---
topic: 进度卡钉死历史 generate 失败
module: video-agent
date: 2026-08-13
keywords:
  - generate_scene_assets_failed
  - nativeAssetsToolSignal
  - AgentPipelineProgress
  - 资产包是对的
---
## 结论摘要
用户看到「资产包角色/场景/道具已正确」但进度卡第 3 步仍红字失败，常见不是当前资产又错了，而是：1) 上一次脏资产导致 generate 秒失败，结构卡仍显示 prepare 产物；2) FE 曾把**所有历史** `generate_scene_assets` failed 聚合成 `nativeAssetsFailedKey`，进度卡被永久钉在失败态。应只看最近一次 Tool 状态，并把 `publicSummary` 写进步骤 detail。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/tools/scene_packages.py`（生图前预校验）

## 核心逻辑
1. `nativeAssetsToolSignal` = 最近一次 generate_scene_assets 的 status/summary
2. failed → 进度失败 + 展示真实摘要；completed/running → 进入生图中
3. Tool 内提前 `_validate_scene_asset_entity_names`，校验失败走 ValidationError 公开文案

## 注意事项
- 「资产包看起来对」≠ 生图已成功；失败发生在 generate，不会回滚 prepare 的结构
- 再点一次模型确认才能发起新的 generate；只改展示不够
