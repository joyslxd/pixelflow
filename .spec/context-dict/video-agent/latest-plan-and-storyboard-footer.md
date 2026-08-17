---
topic: 执行方案只留最新 + 分镜底部操作栏
module: video-agent
date: 2026-08-17
keywords:
  - AgentPlanTimeline
  - planIdsToShow
  - StoryboardPanel
  - 小屏
  - 确认并生成视频
---

## 结论摘要

多轮「合并视频吧」会叠出多张执行方案卡，再加上已完成的「执行规划 · 分镜视频」底栏，
页面像三套规划。改为：对话区只渲染最新一条可展示 Plan，锚到最近用户消息后；已完成的
资产包/分镜视频进度板不再挂在输入框上方。分镜面板把「保存 / 确认并生成」提到
`aside` 底部固定栏，避免小屏单列布局把按钮滚出视口。

## 关键文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/components/canvas/StoryboardPanel.tsx`
- `web/tests/responsiveLayout.test.mjs`
- `web/tests/videoAgentWorkspaceProjection.test.mjs`

## 注意事项

- 历史 Plan 仍在 Snapshot/sessionStorage，只是 UI 不叠卡。
- 进度板仅 `status === "running"` 时显示。
- `preferRicherVideoAgentPlan` 与 reducer 一样：终态优先，避免 awaiting 步骤盖住 completed。
- 有 `videoAgentConfirmation` 时确认卡挂到最新方案；无 confirmation 但仍 awaiting 时显示「重新发起合并」可点按钮。
