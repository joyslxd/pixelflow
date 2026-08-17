---
topic: prepare 结论气泡应打开分镜而非脚本
module: video-agent
date: 2026-08-14
keywords:
  - 查看分镜
  - 在右侧查看脚本
  - AgentTurnGroup
  - prepare_scene_packages
  - turnOffersScenePackageStoryboard
---

## 结论摘要

`prepare_scene_packages` 完成后，Turn 结论气泡曾误用「在右侧查看脚本」入口。用户期望与场景包卡一致：按钮为「查看分镜」，打开分镜资产包画布。

## 相关文件

- `web/src/features/native-video-agent/state/selectors.ts`
- `web/src/features/native-video-agent/chat/AgentTurnGroup.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/tests/nativeVideoAgentTurnGroup.test.mjs`

## 核心逻辑

1. `turnOffersScenePackageStoryboard`：`prepare_scene_packages` 完成或场景包相关文案
2. `turnOffersScriptPreview`：场景包 Turn 互斥，只留给 import/字段/brainstorm
3. `AgentTurnGroup` 优先渲染「查看分镜」→ `onOpenScenePackageStoryboard`
4. LegacyWorkspace 打开时间线最新 `video_scene_packages` 卡的 storyboard

## 注意事项

- 无场景包卡时不传 opener，避免空按钮
- 打开分镜会关闭脚本预览（与 MessageBubble「查看分镜」一致）
