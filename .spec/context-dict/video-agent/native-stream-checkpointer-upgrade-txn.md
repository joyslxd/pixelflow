---
topic: 原生 Agent 真流式、Checkpointer 与升级同事务
module: video-agent
date: 2026-08-12
keywords:
  - astream_events
  - response.delta
  - checkpointer
  - commit_legacy_upgrade
  - __agent_runtime
---

## 结论摘要

`NativeVideoAgentInvoker` 主路径改为 `astream_events(version=v2)`，节流发布
`agent.response.delta` / `agent.reasoning_summary.delta`，结束再发 completed。
Gateway 构造 Invoker 时注入 `app.state.checkpointer`。  
历史升级在 SQL 同库下走 `SQLVideoAgentRepository.commit_legacy_upgrade`：
Workspace 写入与 `orchestration_mode`/`__agent_runtime` 补丁同一事务；
Memory 仍为「先写 Workspace + 失败 discard」补偿。  
Runtime 命名空间键必须是 `__agent_runtime`（不是 `agent_runtime`）。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/events/publisher.py` / `events/native.py`
- `backend/app/gateway/app.py`（checkpointer 注入）
- `backend/pixelflow/video_agent/legacy_upgrade.py`
- `backend/pixelflow/video_agent/workspace/repository.py`（`commit_legacy_upgrade`）
- `backend/pixelflow/video_agent/executor/service.py`（仅 `execute_tool_call` + 抛错桩）
- `backend/pixelflow/video_agent/entrypoint.py`（已删死 Intake 辅助）

## 核心逻辑

1. 流式：`on_chat_model_stream` → delta；`on_chain_end` 取最终 messages。
2. 仅当 `astream_events` 签名不支持 `context=`（TypeError）时降级无 context 流式；
   方法缺失才回退 `ainvoke`（勿把流式中途 AttributeError 当成不可用）。
3. thread_id = `{conversation_id}:{workspace_id}`，依赖 checkpointer 跨请求续跑。
4. SQL 升级：`FOR UPDATE` 锁 conversation + workspace，再同 `session.begin()` 提交。

## 注意事项

- Runtime 命名空间键必须是 `__agent_runtime`（不是 `agent_runtime`）。
- Workspace ID 必须与 Entrypoint 共用 `video_workspace_id_for_conversation`
  （见 `snapshot-409-dual-workspace.md`）；旧 uuid5 命名会双写并弄崩 Snapshot。

- 测流式模型 `_stream` 必须 yield `ChatGenerationChunk(message=AIMessageChunk(...))`，
  直接 yield `AIMessageChunk` 会在 LangChain 缓存路径炸 AttributeError。
- 公开 `response.*` 不得泄漏伪 `<tool_call>` / 虚构工具 JSON（见
  `response-no-fake-tool-call-leak.md`）。
- `LegacyWorkspace.tsx` 体量迁移仍是后续 UI 债；Job HTTP 已卸载、FE stub 已 throw。
- 物理删除 `pixelflow_video.py` 模块文件可再做；当前生产已不挂载路由。
