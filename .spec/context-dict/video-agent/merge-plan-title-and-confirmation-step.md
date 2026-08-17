---
topic: 合并视频确认后 Plan 仍显示生成视频空壳
module: video-agent
date: 2026-08-16
keywords:
  - compose_or_export_video
  - agent.confirmation.requested
  - public_goal
  - 合并视频吧
  - looks_like_creative_followup
  - ReAct
---

## 结论摘要

用户说「合并视频吧」时，活动区已出 `compose_or_export_video` 确认卡（ReAct 正确），但执行方案仍是「处理视频请求：0—10秒｜…」+「0 步 · 规划中」。

根因：

1. `_looks_like_creative_followup` 无 workspace 调用 `looks_like_production_field_reply`，短句一律 True → `_merge_turn_with_workspace_context` 把整篇脚本拼进 `latest_input` → Plan `public_goal` 变成镜头正文。
2. 原生观察 Plan 以 0 步创建；`agent.confirmation.requested` 前端原先要求 step 已存在，缺步直接丢弃 → 一直「规划中」。
3. 确认事件 `title` 原先直接用工具英文名，易被误读成「还在生成视频」。

产品口径：**合并必须走 Agent ReAct**，禁止 compose 确定性 bootstrap。

## 相关文件

- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/tool_gateway.py`
- `web/src/features/video-agent/state/reducer.ts`
- `backend/pixelflow/video_agent/middleware/tool_commitment.py`

## 注意事项

- 「合并视频吧」不得拼回脚本；与 reprepare / confirm script 同级短路。
- Plan 标题优先取【本轮指令】后缀。
- 确认闸门 upsert 步骤标题用中文：`合并分镜视频为成片`。
