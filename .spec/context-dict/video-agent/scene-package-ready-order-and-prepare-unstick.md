---
topic: 场景包就绪后对话顺序与 prepare 进度解卡
module: video-agent
date: 2026-08-14
keywords:
  - awaiting_image_model
  - scene_asset_model_options
  - prepare_scene_packages
  - 选择生图模型
  - 参考图生成中
---
## 结论摘要
确认脚本 → prepare 落库后，正确顺序是：① 进度步骤 2（场景包结构）标完成；② 弹出「视频场景包」卡；③ **立刻**弹出「选择生图模型」卡；④ 用户确认模型后再启动 `generate_scene_assets`。

禁止用 `creationContract.image_model`（表单预填）冒充「已选模」，否则会假显示「参考图生成中」并触发「无 generate_scene_assets 记录」失败文案。进度切到生图中只能跟 assets 步骤真正 `running`（native tool 信号）。

## 相关文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑
1. 投影：`modelConfirmed` 只认 `scene_asset_model_options.sceneAssetModelConfirmed`
2. 结构就绪且无图、无选模卡 → `pushSceneAssetModelOptionsCard`
3. 进度：`hasImages ? completed : assetsStepRunning ? generate_scene_assets : awaiting_image_model`
4. 「没有参考图」自然语言仍可补弹选模卡（兜底）

## 注意事项
- 旧会话若已假卡在「参考图生成中」，刷新后应回到 awaiting + 选模卡
- 生图进行中不要被 snapshot 冲回 awaiting
