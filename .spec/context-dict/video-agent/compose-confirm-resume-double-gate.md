---
topic: 合并确认后二次卡闸未真正 merge
module: video-agent
date: 2026-08-16
keywords:
  - compose_or_export_video
  - native_approved_confirmation
  - tool_call_id
  - confirmation resume
  - 二次确认
---

## 结论摘要

用户点确认返回 200 后，Runner 会 resume；模型重新发起 `compose_or_export_video` 时
`tool_call_id` 往往已变，且确认写入自身会 bump revision。旧闸门要求 call_id 与
`expected_revision` 完全一致，于是再次写入 `native_pending_confirmation`，**合并从未执行**。
Snapshot 原先只投影 Plan 步骤确认，不投影 native pending，刷新后确认卡还会消失。

## 关键文件

- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/pixelflow/agent_runtime/service.py`
- `backend/tests/test_video_agent_tool_gateway_gates.py`

## 核心逻辑

1. 已带 `confirmation_id` 的批准：同 `tool_name` 允许换新 `tool_call_id`，且不再因
   revision 漂移二次拦截。
2. 写入 approved 时 `expected_revision = revision + 1`。
3. Snapshot 优先投影 `native_pending_confirmation`。

## 注意事项

- UI「分镜视频已完成」是 scene_video_progress，不是 merge。
- 修完后应对当前会话再点一次确认（或再说「合并视频吧」）。
