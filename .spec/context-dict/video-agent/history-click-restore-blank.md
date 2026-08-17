---
topic: 点击历史会话无法回显消息
module: video-agent
date: 2026-08-13
keywords:
  - restoreConversation
  - restoreTokenRef
  - setActiveConversationId
  - resumeConversation
  - 历史对话
  - Sidebar
---
## 结论摘要
点左侧历史后对话区空白：恢复 effect 使用了未声明的 `restoreTokenRef`，effect 同步抛错，`resumeConversation` 根本跑不到。叠加 cleanup 里曾 `setActiveConversationId("")`，StrictMode/快切会清空当前会话。修复：声明 `restoreTokenRef`；cleanup 只标 cancelled；切会话先挂目标 id 并清空旧消息再 resume；非 404 错误留在当前路由提示，不盲跳首页。

## 相关文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/layout/Sidebar.tsx`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑
1. `restoreTokenRef` 世代令牌，只允许最新一次 resume 写回
2. cleanup 禁止清空 activeConversationId
3. Sidebar 历史项 `preventDefault` + `navigate(/c/:id)`，避免抽屉打断路由

## 注意事项
- 404 仍回首页（会话确实不存在，常见于换库/清库）
- Thought/工具卡仍依赖 Snapshot hydrate；消息气泡来自 `/resume`
