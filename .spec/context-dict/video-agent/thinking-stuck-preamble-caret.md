---
topic: 思考流卡在固定前言与闪烁光标
module: video-agent
date: 2026-08-12
keywords:
  - 正在核对工作区状态
  - 正在检查生成前置条件
  - showCaret
  - waiting_for_input
  - 视频画幅
  - 结尾行动引导
  - snapshot.hydrated
---

## 结论摘要

用户看到的「正在核对工作区状态。正在检查生成前置条件。▍」里，前两句是 `stream_intake_thinking` 在调 LLM **之前**写死的 reasoning 前言；末尾「〇」是前端流式光标，不是模型输出。服务端该轮往往早已 `publisher.complete()`，并因 Intake 误报 missing 落了 `waiting_for_input`（追问画幅/CTA）。追问话术只在执行方案卡 `public_goal`，思考区 answer 通道故意不写，所以界面像「卡死在前言」。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`（固定前言 + NDJSON progress）
- `backend/pixelflow/video_agent/entrypoint.py`（`reconcile_missing_with_workspace`）
- `backend/pixelflow/video_agent/production_fields.py` / `planner/workspace_digest.py`
- `web/src/lib/supervisor/reducer.ts`（snapshot 不得保留假 streaming）
- `web/src/features/video-agent/AgentPlanTimeline.tsx` / `AgentThinkingStream.tsx`

## 核心逻辑

1. 前言立刻 flush → 等 LLM NDJSON；公开正文不含模型私有 reasoning
2. `needs_user_reply` 时不写 answer 气泡，只写 WAITING Plan
3. Snapshot hydrate：同 turn 已 completed 时覆盖本地 streaming，去掉光标
4. digest 暴露 `has_aspect_ratio` / `has_ending_cta`；Entrypoint 用工作区事实剔除误报 missing

## 注意事项

- 真·卡住 LLM 时也会长时间停在这两句前言（超时 90s）；先看 gateway 是否已有「入场思考流完成」
- 工作区若从未落库画幅/CTA，仍须用户在对话框补充
