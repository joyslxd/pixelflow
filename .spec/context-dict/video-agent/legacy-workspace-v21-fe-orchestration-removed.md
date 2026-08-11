---
topic: LegacyWorkspace 删除 V2.1 禁止的前端编排
module: video-agent
date: 2026-08-11
keywords:
  - LegacyWorkspace
  - resolveWorkflowResumeIntent
  - 关键词编排
  - turns/start
  - frontend_v2 Job
  - Workflow 影子 UI
---

## 结论摘要

按 V2.1 控制面，`LegacyWorkspace` 在 `video_agent_v2` 下不得用关键词/本地 Job 做编排。本轮物理删除：

1. `handleSend` 内整段 `resolveWorkflowResumeIntent` 断点恢复（约 400+ 行）
2. `scriptSkillStages.ts` 中仅服务该编排的关键词检测器整组（确认脚本/重做资产包/开始生图/失败重试/裸继续等）
3. V2 会话不再本地续跑 `pendingScenePackageJob`（仅 `frontend_v2`）

保留：创意确认卡 NL（确认 API）、工作台按钮→Turn、`frontend_v2` Job 客户端、`supervisor_v1` 的 `renderSupervisorVideoArtifact`（V2 已 `return null`）。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/tests/mainFlowContract.test.mjs`

## 核心逻辑

1. V2 NL：创意确认卡以外 → `turns/start` → 思考流 → Plan/Tool
2. 显式 UI 动作（确认脚本/模型/重试参考图/生成分镜）仍可发固定 Turn 文案，不是关键词路由表
3. Job HTTP 与 Job 客户端留给历史 `frontend_v2`（批次 E 未批准硬删）

## 注意事项

- 设计文档写「不重写/删除 LegacyWorkspace 全量」——只删违禁编排，不拆宿主
- 勿把按钮 Turn 文案再做成前端正则主路径
- 缩体积下一优先抽取 projection/controller hook，而不是继续盲删 Job 栈
