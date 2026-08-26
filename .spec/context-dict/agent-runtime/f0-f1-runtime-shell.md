---
topic: F0/F1 公开合同与 Runtime 壳
module: agent-runtime
date: 2026-08-26
keywords:
  - F0
  - F1
  - AgentSnapshotV1
  - PublicAgentEventV1
  - hydrateSnapshot
  - applyPublicEvent
  - harness_v1
  - workspaces/video
---
## 结论摘要

F0 冻结的浏览器合同是 `AgentSnapshotV1` / `PublicAgentEventV1`，事件类型必须使用 `AgentEventType` 全名（`agent.tool.completed`），不能把 Sidecar 内部短名 `tool.completed` 发到浏览器。F1 Runtime 打开对话时用 `GET /agent/conversations/{id}/workspaces/video` 读取或创建工作区，禁止手填 `workspace_id`。SSE 只增量 apply；仅 gap、Tool 完成、Artifact 更新和 Run 终态才回读 Snapshot。`orchestration_mode !== harness_v1` 的旧对话只读。

## 关键文件

- `backend/pixelflow/agent_control_plane/public_contracts.py`
- `backend/tests/fixtures/agent_runtime/harness-snapshot-v1.json`
- `web/src/api/contracts.ts`
- `web/src/features/agent-runtime/reducer.ts`
- `web/src/features/agent-runtime/snapshotProjector.ts`
- `web/src/features/agent-runtime/useAgentConversation.ts`
- `web/src/features/agent-workspace/AgentWorkspace.tsx`

## 核心逻辑

1. Sidecar 事件短名只在 `HarnessRunProjector._public_event` 映射为公开枚举；前端 `normalizeEventType` 再兜一层别名。
2. `hydrateSnapshot` 与按 sequence 逐条 `applyPublicEvent` 必须对同一 fixture 得到相同 `projectVisible` 结果。
3. Turn 的 `workspace_id` / `expected_workspace_revision` 来自 get-or-create 或 Snapshot 投影；网络失败复用同一 `client_input_id`。
4. 新对话 `orchestration_mode=harness_v1`；历史对话 Composer 替换为只读提示。

## 注意事项

- 不要在每条 SSE 后无脑 hydrate；那会把增量流打成轮询。
- F1 不接付费 Tool、InterruptHost、分镜/PPT 面板。
- 前端测试运行器已跳过已删除的 supervisor / video-agent / `lib/api.ts` 模块，新增 Runtime 测试走 `AGENT_RUNTIME_REDUCER_TEST_MODULE`。
