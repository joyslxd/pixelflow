---
topic: 右侧脚本预览默认收起
module: video-agent
date: 2026-08-12
keywords:
  - scriptPreviewOpen
  - AgentScriptPreviewPanel
  - onOpenScriptPreview
  - 查看本步新增
  - 查看分镜
---

## 结论摘要

右侧「脚本预览 · 分阶段产物」默认不展示。仅当用户点击对话内入口时打开：
- 文案中的「已更新/已导入脚本版本 N」链接（`onOpenScriptPreview`）
- 执行方案里的步骤 /「查看本步新增」

「查看分镜」仍打开分镜画布，并关闭脚本预览。面板右上角可收起。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/components/chat/MessageBubble.tsx`

## 核心逻辑

1. `scriptPreviewOpen` 默认 `false`
2. 渲染条件：`!canvasOpen && scriptPreviewOpen && (script || stages)`
3. 切会话 / 脚本清空时复位为收起
4. 不再在「有脚本草稿」时自动关画布撑开预览

## 注意事项

- 确认脚本仍依赖打开预览后的底部「确认」；入口是版本链接或执行方案步骤
- 分镜卡「查看分镜」与脚本预览互斥
