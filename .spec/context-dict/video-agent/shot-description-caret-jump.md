---
topic: 镜头描述打字光标乱跳与整行被删
module: video-agent
date: 2026-08-15
keywords:
  - ShotDescriptionStructuredEditor
  - SceneMentionEditor
  - composeShotDescriptionFields
  - live
  - contentEditable
  - 光标乱跳
---

## 结论摘要

结构化「镜头描述」表格里每键入一字都会：`compose`（`cleanFieldValue` 折叠空格/剥标点）→ 父级改 `text` → `parse` 重建行 → `SceneMentionEditor` `replaceChildren`。contentEditable 光标因此乱跳；删空某字段时 `filter` 掉空值，整行从表格消失，看起来像「删字删整行」。

修复：

1. `composeShotDescriptionFields({ mode: "live" })`：编辑中保留空字段与用户空格
2. `ShotDescriptionStructuredEditor` 本地 `fields` + `emittedTextRef`，外部 `text` 变化才重解析
3. `SceneMentionEditor` 聚焦且正文未变时跳过 DOM 重绘

## 相关文件

- `web/src/lib/shotDescriptionDisplay.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/components/canvas/SceneMentionEditor.tsx`
- `web/tests/shotDescriptionDisplay.test.mjs`

## 注意事项

- 保存落库仍可用默认 `persist` 清洗；表格打字路径必须走 `live`
- 勿在 `onInput` 路径对字段值做 `cleanFieldValue`
- 切分镜时依赖 `text !== emittedTextRef` 同步本地 fields
