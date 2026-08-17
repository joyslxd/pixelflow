---
topic: 确认生图后视频场景包卡片消失
module: video-agent
date: 2026-08-13
keywords:
  - video_scene_packages
  - video-agent-workspace-scene-packages
  - setMessages([])
  - workspaceScenePackageReprojectEpoch
  - resumeConversation
  - generate_scene_assets
---

## 结论摘要

确认生图模型并启动 `generate_scene_assets` 后，对话里「视频场景包」卡可能消失。根因不是 workspace 一定丢了 `scenePackages`，而是：

1. 会话 restore（含同 `conversationId` 的 StrictMode/HMR 重挂）会先 `setMessages([])`；
2. 场景包卡由 FE 从 workspace 投影并 `upsertPersistedChatMessage`；若尚未落库，清气泡后 DB resume 也回不来；
3. 投影 `useEffect` 只依赖 workspace 字段；revision/packages 未变时 **不会重跑**，卡片永久缺失。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（restore + workspace 场景包投影）
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑

1. 仅 `previousConversationId !== conversationId` 时清空气泡；同会话重挂保留本地消息直到 `applyConversation` 原子替换。
2. `video_agent_v2` resume 成功后 bump `workspaceScenePackageReprojectEpoch`，Snapshot 刷新后再 bump 一次。
3. 投影 effect 依赖该 epoch；缺卡（`!existing`）时强制 upsert，结构未变且卡仍在则跳过。

## 注意事项

- 用户说「视频资产包没了」时，先看时间线是否缺 `video_scene_packages`，再查 workspace 是否还有 `scenePackages`。
- 长耗时生图期间热重载最容易触发；不要再对同会话 restore 无条件 `setMessages([])`。
