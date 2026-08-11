---
topic: 资产卡参考图绑定与结构进度、自然语言恢复
module: video-agent
date: 2026-08-10
keywords:
  - scene_asset_model_options
  - reference_brief
  - structure_progress
  - resolveWorkflowResumeIntent
  - 开始生图
  - 继续编辑脚本
  - build_reference_binding_index
---
## 结论摘要
模型选择卡支持上传参考图 + 自然语言用途说明；`generate-scene-assets` 按资产名/类型绑定参考图，角色也可走 `reference_image`。`prepare-scene-packages` 通过 `structure_progress` 暴露结构子阶段（含 deepseek 调用提示）。前端用 `resolveWorkflowResumeIntent` 拦截「继续 / 开始生图 / 继续编辑脚本 / 从断点开始」，按闸门恢复，有 pending Job 时只续查不新开跑。

## 关键文件
- `backend/pixelflow/generate/scene_assets.py`（绑定索引、角色参考图）
- `backend/pixelflow/generate/scene_packages.py` + `pixelflow_video.py`（`structure_progress`）
- `web/src/components/chat/MessageBubble.tsx`（模型卡上传与 brief）
- `web/src/features/video-agent/scriptSkillStages.ts` + `LegacyWorkspace.tsx`（NL 恢复）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`（结构进度投影）

## 核心逻辑
1. 无 brief：上传图全局共享给可参考生图的资产（角色/场景/道具）
2. 有 brief：图 N → 资产名优先，其次类型桶，未匹配进 global
3. prepare 阶段 `on_progress(phase,message)` → Job.`structure_progress` → 执行规划第 2 步 detail
4. NL：pending 优先；awaiting model 复用 sessionStorage 先验模型，否则高亮模型卡；未确认脚本则确认并开资产包

## 注意事项
- 结构模型已切 `deepseek-v4-flash`（API：`deepseek-v4-flash-202605`）；LLM 等待期间每 30s 心跳更新 `structure_progress`
- `reference_brief` / `asset_reference_bindings` 为 Job API 可选新增字段，默认空兼容
- 裸「继续」≤40 字才进 generic resume，避免误伤长文需求
