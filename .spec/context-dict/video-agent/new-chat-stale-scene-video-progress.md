---
topic: 新建对话仍显示上一会话「执行规划 · 分镜视频」
module: video-agent
date: 2026-08-15
keywords:
  - sceneVideoProgressSteps
  - assetPackageProgressSteps
  - resetWorkspace
  - setActiveConversationId
  - 新建对话
  - 执行规划
---

## 结论摘要

新建对话后输入框上方仍挂着上一会话的「执行规划 · 分镜视频」，是因为 `sceneVideoProgressSteps` 未随会话清空：`resetWorkspace` 没清；`currentConversationId === ""` 的 effect 提前 return 也没清；`setActiveConversationId` 切会话只清了资产包进度。

修复：三处都清 `setSceneVideoProgressSteps([])` / `setAssetPackageProgressSteps([])`。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 注意事项

- 同 conversationId 重入不要误清进度（仍用 `previousId !== id` 守卫）
- Composer 插槽用 `sceneVideoProgressSteps.length > 0` 决定是否展示，清数组即可收栏
