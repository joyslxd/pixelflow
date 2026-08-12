---
topic: 下线 SceneEvidencePanel 与待选生图模型按钮
module: video-agent
date: 2026-08-12
keywords:
  - SceneEvidencePanel
  - sceneAssetsAwaitingModel
  - 待选择生图模型
  - 参考图追问
  - AgentScriptPreviewPanel
---

## 结论摘要

右侧「分镜 · revision / 编辑此镜头 / 质检问题」来自 `SceneEvidencePanel`：prepare 写入 `payload.scenes` 后 `useVideoAgent` 自动选中首镜，证据面板抢占脚本预览。已从 `LegacyWorkspace` 卸载该面板。

「待选择生图模型」是 `MessageBubble` 在 `sceneAssetsAwaitingModel` 时的禁用按钮。改为：结构卡只保留「查看分镜」，并在对话气泡追问是否有参考图；用户在对话框回复/上传后走 Turn 收集信息再生图。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/features/video-agent/SceneEvidencePanel.tsx`（组件文件保留，页面不再挂载）
- `web/tests/videoAgentWorkspaceProjection.test.mjs`

## 核心逻辑

1. 右侧优先 `AgentScriptPreviewPanel`，不再分支到证据面板
2. `sceneAssetsAwaitingModel` 时不渲染第二枚按钮；生成中仍显示「参考图生成中」
3. V2 workspace upsert 与旧 job `awaiting_image_model` 阶段：推送参考图追问气泡，不再 `pushSceneAssetModelOptionsCard`

## 注意事项

- `SceneEvidencePanel.tsx` 仍可被单测读到文案；页面契约改为 `doesNotMatch(/SceneEvidencePanel/)`
- 用户说「没有参考图，直接生成」需依赖 Planner/Turn 选 `generate_scene_assets`；若漏规划再补思考流规则
