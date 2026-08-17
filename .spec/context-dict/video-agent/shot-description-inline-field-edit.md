---
topic: 镜头描述字段内联 @ 编辑
module: video-agent
date: 2026-08-14
keywords:
  - ShotDescriptionStructuredEditor
  - SceneMentionEditor
  - composeShotDescriptionFields
  - 编辑并 @ 参考图素材
---

## 结论摘要

分镜面板「镜头描述」曾先只读表格再单独挂一块「编辑并 @ 参考图素材」。现改为：结构化字段表格内每个值格直接用紧凑 `SceneMentionEditor`；非结构化则只保留一个可 @ 编辑器。写回仍是整段 `shot_description.text`，经 `composeShotDescriptionFields` 拼回。

## 相关文件

- `web/src/lib/shotDescriptionDisplay.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/src/components/canvas/SceneMentionEditor.tsx`
- `web/tests/shotDescriptionDisplay.test.mjs`

## 注意事项

- 单字段改动会合并全镜 mentions，并按正文仍出现的 `@名/@asset_id` 裁剪
- 不要把表格字段拆成后端独立列，除非契约升级
- `compact` 编辑器不单独显示「已关联 N/9」，计数在表格下方统一展示
- 打字光标乱跳见 `shot-description-caret-jump.md`：编辑须 `compose(..., { mode: "live" })` + 本地 fields
