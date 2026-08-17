---
topic: Snapshot 409 因同会话双 Workspace 身份不一致
module: video-agent
date: 2026-08-13
keywords:
  - agent-snapshot
  - 409
  - AgentRuntimeRecordConflictError
  - video_workspace_id_for_conversation
  - legacy_upgrade
---

## 结论摘要

`GET .../agent-snapshot` 在 Turn 后变 409，不是 CAS 版本冲突，而是 Snapshot 投影
`load_conversation_state` 发现同会话两个 Workspace 抛
`AgentRuntimeRecordConflictError` → `agent_runtime_interrupt_state_invalid`。

根因：`legacy_upgrade` 用 `uuid5(pixelflow-video-workspace:…)`，Entrypoint 用
`video_workspace_{uuid5(pixelflow-video-agent:video_workspace:…)}`，升级壳 + Turn
各建一个。已统一为 `video_workspace_id_for_conversation`；多 Workspace 时择权威并
删除无 Plan 孤儿。

## 相关文件

- `backend/pixelflow/video_agent/workspace/ids.py`
- `backend/pixelflow/video_agent/legacy_upgrade.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/workspace/repository.py`
- `backend/pixelflow/agent_runtime/service.py`（Snapshot 映射 409）

## 核心逻辑

1. 权威 ID = `video_workspace_{uuid5(...).hex}`
2. 加载时：稳定 ID > 有 Plan > 最近更新；无 Plan 孤儿 discard
3. 响应 body `detail.code` 多为 `agent_runtime_interrupt_state_invalid`

## 注意事项

- 已污染的本地 sqlite 可删 `workspace_id NOT LIKE 'video_workspace_%'` 且无 plan 的行
- 刷新 Snapshot 也会自愈删除孤儿
- 勿再引入第二套 workspace 派生规则
