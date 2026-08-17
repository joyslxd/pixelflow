---
topic: 镜头预览放大覆盖全屏
module: video-agent
date: 2026-08-15
keywords:
  - 镜头预览
  - previewExpanded
  - StoryboardPanel
  - Maximize2
  - 返回分镜编辑
---

## 结论摘要

分镜面右侧「镜头预览」可点击放大：`fixed inset-0` 覆盖左侧对话与中栏素材编辑；顶栏「返回」或 Esc 回到双栏编辑。放大层保留主预览与分镜缩略条，便于切镜审片。

## 相关文件

- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/tests/videoSceneUiContract.test.mjs`

## 注意事项

- 放大层 z-index 高于分镜 aside（z-50）与素材弹层；关闭放大不关闭分镜面本身
- 不改动「保存 / 确认并生成」主路径；放大态以审片为主
