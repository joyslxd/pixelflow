---
topic: 方案卡回执顺序与 characters 假卡死
module: video-agent
date: 2026-08-10
keywords:
  - agent-ack
  - resolveVideoAgentPlanAnchorId
  - SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS
  - asyncio.to_thread
  - stale RUNNING
  - /characters
---
## 结论摘要
1. 执行方案卡曾锚在用户消息后，回执「已收到创作请求…」排在卡片后面。现优先锚到回执消息。
2. `/characters` 显示跑 20 分钟：多半是 LLM `ainvoke` 堵住事件循环，`wait_for(180)` 无法生效；热重载 CancelledError 也会留下 RUNNING 僵尸步。修复：Skill 阶段改 `to_thread(model.invoke)` + 180s 超时；Snapshot 对陈旧 RUNNING 自动 `resume`。

## 关键文件
- `web/src/lib/videoAgentPlanAnchor.ts` + `LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/executor/service.py`
- `backend/pixelflow/agent_runtime/service.py`（Snapshot 拉起 stale resume）

## 核心逻辑
1. `resolveVideoAgentPlanAnchorId`：用户消息后的 ack > 用户消息
2. Skill LLM：`asyncio.wait_for(asyncio.to_thread(model.invoke, …), 180)`
3. `maybe_resume_stale_running_plan`：RUNNING 超过 180+30s 时重跑该步

## 注意事项
- 当前已卡死的会话：刷新页面触发 Snapshot 恢复；或新开对话
- 开发 `--reload` 仍可能在超时前杀掉任务，靠 stale resume 兜底
- 超时软完成仍可能让后续 stage 缺 characters 内容，需人工重跑该步/新 Turn
- 陈旧重跑会刷新 `started_at`，避免 UI 把僵尸等待显示成「跑了几十分钟」（见 `episode-zombie-duration-after-reload.md`）
