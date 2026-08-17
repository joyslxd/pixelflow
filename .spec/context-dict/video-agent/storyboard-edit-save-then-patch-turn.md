---
topic: 分镜编辑须先保存再发修改 Turn
module: video-agent
date: 2026-08-14
keywords:
  - deferSceneUpdates
  - sceneDraftPatches
  - handleUpdateVideoScenePackage
  - 修改分镜
  - SceneMentionEditor
  - 保存
---

## 结论摘要

V2 分镜画布曾把 `deferSceneUpdates` 设为 false，导致 `SceneMentionEditor` 每次按键 / `@` 选素材都调用 `handleUpdateVideoScenePackage` → 立刻发「修改分镜 …」Turn，打断本地编辑。正确交互：本地草稿编辑 → 点「保存」冲洗草稿 → 再发自然语言 Turn → bootstrap `patch_scene`。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/tests/videoSceneUiContract.test.mjs`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑

1. `deferSceneUpdates={orchestrationMode === "video_agent_v2" || Boolean(supervisorVideoArtifact)}`
2. 原生 `ScenePackageCanvas` 固定 `deferSceneUpdates`
3. 有未保存草稿时禁止切换镜头（「请先保存当前分镜」）
4. `saveStoryboardDraft` → `onUpdateVideoScenePackage(草稿)` → 一次 Turn

## 注意事项

- 不要在 `onChange` 路径直接 `handleSupervisorTurn`
- 保存后画布仍会关闭（既有 `handleSaveVideoScenePackage` 行为）；Turn 在关闭前已提交
