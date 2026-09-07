---
topic: harness-runs/events 在 DevTools 里看起来很慢
module: agent-runtime
date: 2026-09-04
keywords:
  - after_sequence
  - SSE
  - agent.response.delta
  - DeepSeek
  - list_events
---
## 结论摘要

`GET /agent/conversations/{id}/harness-runs/{run_id}/events?after_sequence=N` 是长连接 SSE，不是普通 REST。Chrome 里这条请求的耗时等于「等到后面还有事件，或 Run 结束」。`after_sequence=2377` 是会话级序号，不代表这一轮模型吐了 2377 条。

## 关键文件

- `backend/app/gateway/routers/pixelflow_conversations.py`（`stream_harness_run_events`，80ms 轮询 Outbox）
- `web/src/api/agentRuntime.ts`（`streamHarnessEvents`）
- `backend/pixelflow/agent_harness/projector.py`（`events_after` 目前 `list_events` 全表再过滤）

## 核心逻辑

1. 浏览器每次只从已确认 sequence 之后续传；连接一直开着，直到 `completed/failed/cancelled` 或客户端断开。
2. 新事件只在 Sidecar 收到模型/Tool 结果并写入 Outbox 后才出现。首包 `response.delta` 之前的空白，主要是供应商思考/首 token，不是 SQLite 扫描。
3. 实测 `hrun_d392fdc61c68bf2ec849ef8fb3cd6dca`：会话序号 2375–2709，本 Run 约 335 条公开事件；Sidecar 389 条。思考开始 15:43:16Z，第一条 `response.delta` 15:44:37Z（约 81s），收尾 Tool 各约 30ms。会话 2738 行全表读取约 14ms，不是主因。
4. `agent.response.delta` 条数多，是流式 token 一包一条，payload 通常十几字节。

## 注意事项

- 不要把 SSE pending 当成接口超时或 Gateway 卡死。
- `projector.events_after` 每 80ms `list_events` 全会话，事件很多时会增加 CPU，但解释不了几十秒级等待。
- 不要把用户正文、Authorization 或模型原始输出写进日志/交接。
