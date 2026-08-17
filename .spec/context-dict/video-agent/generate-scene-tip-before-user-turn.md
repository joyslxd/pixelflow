---
topic: 确认并生成分镜时助手提示抢在用户 Turn 前落库
module: video-agent
date: 2026-08-15
keywords:
  - pushAssistant
  - 正在生成分镜
  - handleGenerateVideoFromScenePackages
  - handleSupervisorTurn
  - 消息顺序
---

## 结论摘要

点「确认并生成分镜 N」后，FE 先 `pushAssistant(正在生成分镜 N…)` 并持久化，再 `handleSupervisorTurn` 写用户消息。刷新按 `created_at` 排序时助手气泡排在用户 Turn 前面；当时页面也像「无响应」（真正反馈在 Turn/流式活动里）。

修复：V2 路径不再在 Turn 前抢跑 tip；用户消息 → 原生 bootstrap/活动/最终回复。同类问题一并修了失败参考图重试、QC 重生成。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/tests/mainFlowContract.test.mjs`

## 注意事项

- 历史已颠倒的消息不会自动重排
- V2 进度靠 Turn 流式活动 + 底栏「执行规划 · 分镜视频」，不需要本地「正在生成…」占位气泡
