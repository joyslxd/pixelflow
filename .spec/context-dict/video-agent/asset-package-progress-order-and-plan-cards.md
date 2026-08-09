---
topic: 资产包进度卡顺序与执行方案卡消失
module: video-agent
date: 2026-08-09
keywords:
  - assetPackageAnchorMessageId
  - replaceOptimisticMessage
  - agentActivityBlocks
  - orphanActivityBlocks
  - videoAgentPlanAnchors
  - resolveAssetPackageProgressAnchorId
---
## 结论摘要
「执行规划 · 视频资产包」错位到脚本确认前，是因为进度卡锚点用了乐观消息 client id；落库后 id 被替换，锚点失效，旧逻辑回落到**首条用户消息**。点击「同意方案」不会产生新用户消息，于是进度卡顶到对话前段。同源问题：执行方案锚点失效后变 orphan，以前渲染在对话最底部，看起来像「执行方案卡消失」——尽管 Snapshot.plans 可能仍在。

## 关键文件
- `web/src/lib/assetPackageProgressAnchor.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/chat/ChatPanel.tsx`
- `.spec/context-dict/video-agent/plan-history-server-persist.md`

## 核心逻辑
1. `startVideoAgentAssetPackageFromScript` await 落库后的真实 notice message id 再设锚点
2. `replaceOptimisticMessage` 同步重映射 `assetPackageAnchor` / `videoAgentPlanAnchors`
3. 进度卡 fallback：notice 文案 → 脚本确认卡 → 最近助手消息；**禁止**回落用户消息
4. 执行方案缺锚点时回落首条用户消息；orphan 也挂到首条用户消息后
5. 切会话先 `loadVideoAgentPlanHistory`；runtime 空时不把热缓存冲成空

## 注意事项
- 后端 chat / plans 持久化仍然有效；这次是前端锚点错位导致“看不见”
- 刷新后依赖 conversation context 里的 `asset_package_anchor_message_id` + Snapshot.plans
