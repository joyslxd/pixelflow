---
topic: AgentEvent sequence 并发冲突导致确认/工具事件丢失
module: video-agent
date: 2026-08-16
keywords:
  - AgentEvent sequence 必须连续递增
  - create_event
  - NativeAgentEventPublisher
  - agent.confirmation.requested
  - agent.tool.started
  - TOCTOU
---

## 结论摘要

合并视频时日志出现：

- `emit agent.tool.started failed` / `confirmation.requested` / `tool terminal`
- `AgentRuntimeRecordConflictError: AgentEvent sequence 必须连续递增`

根因：各处先 `list_events` 算 `next=last+1`，再 `create_event` 校验。分镜 Operation 回写、思考流、工具进度、确认闸门**并发**写同一 conversation 时，锁外读到的序号过期 → 整条公开事件被丢掉（此前只打 ERROR 继续）。确认卡因此可能不出现；随后模型节点再撞上游 500 是另一问题。

修复：Memory/SQL `create_event` 在写锁内若 `provided < expected` 则改写为 `expected`（自愈 TOCTOU）；`provided > expected` 仍 fail-closed（禁止跳号）。

## 相关文件

- `backend/pixelflow/agent_runtime/persistence/repositories.py`
- `backend/tests/test_agent_runtime_event_outbox.py`
- `backend/pixelflow/video_agent/events/publisher.py`（仍预读 sequence，依赖仓库自愈）
- `backend/pixelflow/video_agent/tool_gateway.py`

## 注意事项

- 重启 Gateway 后生效
- 日志里若还有 `openai.InternalServerError code 1000`，是模型网关 500，与 sequence 无关，需重试或查上游
- 跳号（故意传更大 sequence）测试仍应失败
