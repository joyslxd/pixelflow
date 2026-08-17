---
topic: 分镜失败原因可读化、场景包预览回填、失败重试按钮
module: video-agent
date: 2026-08-14
keywords:
  - failed_scenes
  - formatSceneVideoFailureReason
  - generatedSceneVideos
  - StoryboardPanel
  - 重新生成失败分镜
---

## 结论摘要

1. 失败原因原先只剩「供应商任务执行失败。」，用户看不懂。FE 用 `formatSceneVideoFailureReason` 展开为「第 N 镜 + 业务失败含义 + 可重试」。
2. 成片 URL 只写在独立「分镜视频」卡，未回填「视频场景包」artifact，分镜面板镜头预览仍是参考图。修复：upsert 时同步 `generatedSceneVideos` 到场景包消息，打开面板时再从 Workspace 合并一遍。
3. 「重新生成场景视频」依赖 `videoGenerationFailed` 条件过严。失败列表存在时固定展示「重新生成失败分镜」，走既有 Turn `继续生成失败的分镜视频`。

## 相关文件

- `web/src/lib/sceneVideoFailures.ts`
- `web/src/components/chat/MessageBubble.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/state/workspace.ts`

## 注意事项

- content-app 失败详情被 Provider Adapter 安全抹平，前端只能解释 reason_code，不能回显供应商原文
- 打开旧场景包卡若尚未同步，依赖 Workspace merge；刷新后会持久化到消息
