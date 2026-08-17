---
topic: 设定字段名误抽导致参考图秒失败
module: video-agent
date: 2026-08-13
keywords:
  - 视觉特征
  - 动作习惯
  - 人物弧光
  - 分镜提示词
  - generate_scene_assets
  - 工具执行失败
  - AgentPipelineProgress
---
## 结论摘要
脚本预览设定若用「视觉特征/时段/分镜提示词」等字段作标题，会被抽成 global_assets；生图边界 `asset_requirement_entity_quality_issues`（如「光线」）秒拒 → `generate_scene_assets` 0s 失败。旧 bootstrap 把 Registry 失败结果当成成功并回复「已启动」，进度卡继续空转。修复：字段标签黑名单 + 分镜段截断；字段资产占比高时生图前重拆；失败公开摘要带工具名；进行中/失败进度卡挂输入框上方常驻。

## 关键文件
- `backend/pixelflow/creative/asset_manifest.py`
- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/tools/registry.py`
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/src/components/chat/ChatPanel.tsx`（`composerTopSlot`）

## 核心逻辑
1. `_SETTING_FIELD_LABELS` 拒绝字段名；`## 分镜提示词` 不再并入道具段
2. `_workspace_global_assets_look_like_field_labels` → 生图前 prepare 重拆
3. `_generate_scene_assets_result_failed` 禁止假成功；活动行展示 `generate_scene_assets`
4. 进度卡 running/failed 时 `composerTopSlot` 常驻，避免中段空转

## 注意事项
- 已生成的脏包需再确认模型（或重拆）后才会变正确
- 「执行规划 · 视频资产包」是前端固定分步卡，不是 Agent Plan steps
