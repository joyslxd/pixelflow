---
topic: 视频资产包 creation_contract 与对话时间线锚定
module: video-agent
date: 2026-08-08
keywords:
  - prepare-scene-packages
  - creation_contract
  - image_model_capabilities
  - agentActivityBlocks
  - AgentPipelineProgress
---
## 结论摘要
从脚本「继续生成视频」走 `startVideoAgentAssetPackageFromScript` 时，`creation_contract` 必须带齐后端 `VideoCreationContract` 必填字段（至少 `image_model_capabilities`、`video_usage`），否则 `/prepare-scene-packages/start` 会 422。对话里脚本执行方案应锚在首条用户消息后，资产包分步进度应锚在「继续生成视频」那条用户消息后，不能统一挂在消息列表末尾。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`buildVideoAgentCreationContract` / `startVideoAgentAssetPackageFromScript` / `agentActivityBlocks`）
- `web/src/components/chat/ChatPanel.tsx`（`agentActivityBlocks` 按 `afterMessageId` 插入）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`
- `backend/pixelflow/creative/contract.py`

## 核心逻辑
1. FE 构造的 contract 对齐 `build_video_creation_contract` 默认值：`image_model_capabilities`、`video_model_capabilities`、`video_usage` 等。
2. ChatPanel 用 `agentActivityBlocks` 把活动卡片插到指定消息之后；兼容旧 `agentActivity` 时也默认跟首条用户消息。
3. 资产包 Job 的 `stage`（`prepare_scene_packages` → `generate_scene_assets` → `completed`）驱动 `AgentPipelineProgress` 分步展示；进度卡锚在「已收到…」助手回执下方，而不是用户消息下方。

## 注意事项
- 只传部分 contract 字段时，Pydantic 会按「已提供的对象」校验，缺必填项直接 422，不会回落到后端 default_factory。
- 脚本直出资产包时还必须带上 `scene_image_ratio` + `scene_image_size`（prepare/generate 阶段 `_scene_image_size` 不允许 null），默认 `9:16` / `4K`（对齐 Borg `gpt-image-2` 默认，勿用视频档 `1080p`）。
- 启动前优先拉 content-app 图片模型能力；后端对 `gpt-image-2 + 1080p` 会 coerce 到 `4K`。
- 脚本 8 步卡片与资产包进度是两套 UI；继续生成不要再开一轮脚本 plan。
