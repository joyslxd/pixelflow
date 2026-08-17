---
topic: 右侧脚本预览默认收起
module: video-agent
date: 2026-08-13
keywords:
  - scriptPreviewOpen
  - AgentScriptPreviewPanel
  - onOpenScriptPreview
  - AgentTurnGroup
  - 在右侧查看脚本
  - turnOffersScriptPreview
---

## 结论摘要

右侧「脚本预览 · 分阶段产物」默认不展示。仅当用户点击对话内入口时打开：
- 原生 Turn 结论气泡：
  - 脚本类 Turn →「在右侧查看脚本」（`turnOffersScriptPreview`）
  - 场景包/prepare Turn →「查看分镜」（`turnOffersScenePackageStoryboard`，打开分镜资产包）
- 文案中的「已更新/已导入脚本版本 N」或「在右侧查看脚本」链接（`onOpenScriptPreview`）
- 执行方案里的步骤 /「查看本步新增」

面板右上角可收起，收起后可再次从对话入口点开。「查看分镜」仍打开分镜画布，并关闭脚本预览。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/native-video-agent/chat/AgentTurnGroup.tsx`
- `web/src/features/native-video-agent/state/selectors.ts`
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/components/chat/MessageBubble.tsx`
- `backend/pixelflow/video_agent/native_invoke.py`（字段齐备后引导点「在右侧查看脚本」）

## 核心逻辑

1. `scriptPreviewOpen` 默认 `false`
2. 渲染条件：`!canvasOpen && scriptPreviewOpen && (script || stages)`
3. 切会话 / 脚本清空时复位为收起
4. 不再在「有脚本草稿」时自动关画布撑开预览
5. `turnOffersScriptPreview`：回答已落定，且完成过 `import_script` / `apply_production_fields` / `brainstorm_script`，或文案含脚本就绪语义时，才展示按钮；外层仅在 workspace 确有脚本时传入 `onOpenScriptPreview`

## 注意事项

- 确认脚本仍依赖打开预览后的底部「确认」；入口是结论按钮、版本/CTA 链接或执行方案步骤
- 分镜卡「查看分镜」与脚本预览互斥
- 补全生产字段后的公开回复必须点名对话内入口，不能假设右侧已展开
