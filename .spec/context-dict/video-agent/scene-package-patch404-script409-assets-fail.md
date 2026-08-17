---
topic: 场景包 PATCH 404、脚本 409 与参考图失败吞因
module: video-agent
date: 2026-08-13
keywords:
  - upsertPersistedChatMessage
  - video-agent-workspace-scene-packages
  - saveVideoAgentScript
  - 409
  - generate_scene_assets
  - 场景包/参考图 Operation 执行失败
---

## 结论摘要

用户同时看到三类报错时，常见是同一会话里的连锁问题：

1. **PATCH …/messages/video-agent-workspace-scene-packages:… → 404**：`upsertPersistedChatMessage` 对尚未落库的 client_message_id 先 PATCH，必然 404，再 create。已改为「未落库先 create；已落库才 PATCH」。
2. **PUT …/video-agent/script → 409**：`expected_revision` 过期。保存/确认改为最多 3 次「刷新 Snapshot → 用新 revision 重试」。
3. **generate_scene_assets「Operation 执行失败」**：Provider 合同把失败 message 归一成固定文案，领域原因被吞。Port 在 FAILED 时回读 `ExistingJobService.status` 的 `message` 再抛出。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `web/src/lib/supervisor/api.ts`
- `backend/pixelflow/video_agent/adapters/scene_package_operation.py`
- `backend/tests/test_video_agent_scene_package_operation.py`

## 核心逻辑

1. `persistedChatMessageIdsRef` 记录已落库 id；恢复会话时从 messages 种子化
2. `saveVideoAgentScriptWithRevisionRetry` 读 `supervisorRuntime.state.videoAgentWorkspace.current.revision`
3. `_terminal_failure_detail` → 领域 status.message → 用户可见 Tool 失败摘要

## 注意事项

- PATCH 404 本身不一定阻断 create；但噪音 + create 静默失败会让卡片不落库
- 参考图若仍失败，新文案应带具体业务原因（如字段名当资产）；按原因修设定或重拆
- ProviderJobSnapshot 合同仍禁止 FAILED 带 result，勿轻易改六态文案
