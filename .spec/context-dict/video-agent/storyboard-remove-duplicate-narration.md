---
topic: 场景包底部重复旁白字段删除
module: video-agent
date: 2026-08-15
keywords:
  - StoryboardPanel
  - narration
  - 旁白（对白）
  - ShotDescriptionStructuredEditor
---

## 结论摘要

分镜面板底部独立「旁白」textarea 与镜头描述六字段里的「旁白（对白）」重复。已删除底部字段；旁白只在结构化镜头描述中编辑。`scene.narration` 仍可能由后端/旧包带入，保存 patch 路径保留，但 UI 不再单独展示编辑框。

## 相关文件

- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/tests/videoSceneUiContract.test.mjs`

## 注意事项

- 六字段标签是「旁白（对白）」，在 `ShotDescriptionStructuredEditor`
- 勿把 `selectedScene.narration` 表单再加回来
