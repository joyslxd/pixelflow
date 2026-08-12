---
topic: 场景包 Operation 启动失败因 start lease 过短
module: video-agent
date: 2026-08-12
keywords:
  - 场景包/参考图 Operation 启动失败
  - Operation start租约无效
  - lease_duration
  - prepare_scene_packages
  - M06ScenePackageOperationPort
---

## 结论摘要

`prepare_scene_packages` 领域 Job **同步等待 LLM**，常超过 1 分钟。`OperationStartCoordinator` 默认 start lease 仅 **30 秒**；LLM 返回后 `finalize_operation_start_terminal` 因 `lease_expires_at` 过期抛「Operation start租约无效」，被包装成 UI「场景包/参考图 Operation 启动失败」。修复：场景包 Port 使用 **15 分钟** start lease；stage 指纹纳入 form_values，避免补画幅后与旧 request_hash 冲突。

## 相关文件

- `backend/pixelflow/video_agent/adapters/scene_package_operation.py`
- `backend/pixelflow/agent_runtime/jobs/recovery.py`（默认 lease 30s）
- `backend/pixelflow/agent_runtime/persistence/repositories.py`（`finalize_operation_start_terminal`）
- `backend/tests/test_video_agent_scene_package_operation.py`

## 核心逻辑

1. claim start lease（默认 30s）→ adapter.start（同步 LLM）→ finalize 终态
2. `completed_at > lease_expires_at` → Conflict → Tool「启动失败」
3. Port 传入 `lease_duration=15min`；错误信息带上冲突原因摘要

## 注意事项

- 日志里可能没有 `prepare_scene_packages domain job failed`（LLM 已成功，死在租约落库）
- 周期性 `Operation 恢复候选失败：completion_dispatch` 是另一条脏数据扫描，与本次不同
- 长期应把 prepare 改成异步 polling，而不是无限加长 lease
