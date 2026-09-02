---
topic: 打开历史对话停在第一轮
module: agent-runtime
date: 2026-09-02
keywords:
  - ConversationMessages
  - scrollTop
  - 历史对话
  - 最后一轮
---
## 结论摘要

消息按 `created_at` 正序渲染，滚动容器默认 `scrollTop=0`，打开长历史会话会停在第一轮。进入或切换会话时应钉到内容底部；用户上翻后不再强行拉回。

## 关键文件

- `web/src/features/conversations/ConversationMessages.tsx`
- `web/src/lib/conversationScroll.ts`
- `web/src/features/agent-workspace/AgentWorkspace.tsx`

## 核心逻辑

1. 切换 `conversationId` 时重置贴底标记。
2. `useLayoutEffect` + `ResizeObserver` 在消息/预览增高后把 `scrollTop` 设为 `scrollHeight`。
3. 距底部超过 80px 视为阅读历史，后续增量不再自动贴底。

## 注意事项

- 不要把消息改成倒序来“看起来在底部”。
- 确认卡在 Composer 区域，不在消息列表里；贴底后仍能看到最新气泡。
