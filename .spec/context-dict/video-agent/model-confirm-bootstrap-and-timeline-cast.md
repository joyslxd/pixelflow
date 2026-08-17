---
topic: 模型确认空转与时间线资产清空
module: video-agent
date: 2026-08-13
keywords:
  - 确认生图模型
  - generate_scene_assets
  - 已完成本轮处理
  - extract_dialogue_cast_assets
  - _align_global_assets_to_blueprints
  - global_assets
---

## 结论摘要

1. FE 模型卡确认后 Turn「确认生图模型 …」若只靠模型自发选 Tool，常空转「已完成本轮处理」；进度条「正在生图」只是前端乐观态。须 bootstrap 直执 `generate_scene_assets`（模型卡即确认闸门）。
2. `0—10秒｜` 时间线拆镜的蓝图 `asset_requirements` 为空时，`_align_global_assets_to_blueprints` 会把角色/场景/道具清空；无设定章节时还需从对白说话人兜底抽资产。

## 相关文件

- `backend/pixelflow/video_agent/native_invoke.py`（`_bootstrap_generate_scene_assets_if_needed`）
- `backend/pixelflow/creative/asset_manifest.py`（`extract_dialogue_cast_assets`）
- `backend/pixelflow/generate/scene_packages.py`（align 空需求时保留）

## 核心逻辑

1. 解析「确认生图模型 {model}，比例 …，清晰度 …」→ Registry 直执 generate
2. 生图前若 global_assets 无名资产 → 先 prepare 补结构
3. 无设定章节 → 对白说话人 + 地点词 + 产品后缀抽种子
4. 蓝图 requirements 全空 → 不对齐清空，保留已有 global_assets

## 注意事项

- Gateway `confirmation_required` 仍拦自然语言 Tool Call；bootstrap 绕过是因为 FE 模型卡已确认
- 旧对话空资产需再点一次模型确认或重拆后才能看到角色/场景/道具
- 启动成功后必须短接，禁止再进模型（见 `generate-scene-assets-fake-running-after-model-confirm.md`）
