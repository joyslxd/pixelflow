---
topic: 三视图 gpt-image-2 1080p 无价格配置
module: video-agent
date: 2026-08-08
keywords:
  - gpt-image-2
  - scene_image_size
  - 1080p
  - 4K
  - 模型价格配置不存在
  - text_to_image
  - three_view
---
## 结论摘要
三视图失败 `模型价格配置不存在: modelType=gpt-image-2, size=1080p` 不是 channelId 问题（Borg 文档写明可不传 channelId），而是 **把视频清晰度 1080p 当成了生图清晰度**。Borg Skill `DEFAULT_IMAGE_QUALITY_BY_MODEL['gpt-image-2']='4K'`；脚本直出资产包曾硬编码 `scene_image_size=1080p`，content-app 对该组合无计费档。

## 关键文件
- `web/.../LegacyWorkspace.tsx`（`buildVideoAgentCreationContract` / `resolveVideoAgentCreationContract`）
- `web/src/lib/videoRequirementConfig.ts`（`preferredImageSize`）
- `backend/pixelflow/generate/scene_assets.py`
- `backend/app/gateway/routers/pixelflow_video.py`（`_coerce_scene_image_size`）
- `backend/pixelflow/skills/borgrise/run_generation.py`（权威默认 4K）

## 核心逻辑
1. 合同默认 / 首选：`4K > 2K > 1080p`
2. 启动资产包前尽量拉 `listByCategory/image_generate` 实时能力
3. 后端对 `gpt-image-2 + 1080p` 强制回落 Skill 默认 `4K`

## 注意事项
- `channelId=null` 可忽略；真正缺的是该 model+size 的价格行
- 旧会话里已写入的错误 contract 也会被后端 coerce 兜住
- 视频 `video_size=1080p` 与图片 `scene_image_size` 必须分开，勿互相套用
