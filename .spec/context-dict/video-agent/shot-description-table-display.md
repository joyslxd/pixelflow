---
topic: 镜头描述表格化展示
module: video-agent
date: 2026-08-09
keywords:
  - shot_description
  - parseShotDescriptionFields
  - StoryboardPanel
  - 镜头描述
---
## 结论摘要
分镜面板里的「镜头描述」原是整段可编辑文本。现先解析为时间/地点/角色/道具/景别等字段，用表格分段展示；`@` 引用高亮。结构化时默认折叠原文编辑，点「编辑原文」再打开 `SceneMentionEditor`。

## 关键文件
- `web/src/lib/shotDescriptionDisplay.ts`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/tests/shotDescriptionDisplay.test.mjs`

## 核心逻辑
1. `parseShotDescriptionFields` 识别时间范围与标签字段（地点/角色/道具/景别等）
2. 无法识别时回落单行「描述」并保持编辑器常开
3. 展示层只读表格，不改存储契约（仍是 `shot_description.text`）

## 注意事项
- 不要把表格字段拆写回独立后端字段，除非契约升级
- 切换分镜会收起原文编辑
