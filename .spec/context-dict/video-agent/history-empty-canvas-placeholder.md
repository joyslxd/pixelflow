---
topic: 历史进入对话右侧空「画布」占位
module: video-agent
date: 2026-08-15
keywords:
  - canvasOpen
  - CanvasPanel
  - applySnapshot
  - ArtifactCanvasRouter
  - 画布
---

## 结论摘要

右侧「画布」是旧 frontend_v2 的 `CanvasPanel` 壳（Brief / 进度 / 成片预览）。V2 真正要用的是点「查看分镜」打开的场景包面板。历史恢复时会话 context 里 `canvasOpen=true`，但会清空 `selectedStoryboardMessageId`，于是落到空占位文案「Brief、生成进度与成片会展示在这里。」

修复：恢复时有场景包/方案/旧 Brief 内容才打开并回填选中；否则关闭。快照也不再持久化空占位的 `canvasOpen`。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/canvas/CanvasPanel.tsx`

## 注意事项

- 需要分镜时仍从对话「查看分镜」/场景包卡打开，不会默认空开右侧
- 旧会话 context 里已存的 `canvasOpen=true` 靠恢复逻辑纠正，不必手工清库
