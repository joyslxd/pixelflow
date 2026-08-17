---
topic: compose 同步合并成功但 Operation 启动失败（start 租约 30s 过期）
module: video-agent
date: 2026-08-17
keywords:
  - compose_or_export_video
  - 视频交付Operation启动失败
  - Operation start租约无效
  - OperationConflictError
  - M06DeliveryOperationPort
  - content-app /api/video/merge
---
## 结论摘要

content-app 同步 `/api/video/merge` 常需 1 分钟以上（14 镜实测 ~65s）。`M06DeliveryOperationPort` 原先未传 `lease_duration`，`OperationStartCoordinator` 默认 **30s** start 租约。Provider 已返回 SUCCEEDED 后 `finalize_operation_start_terminal` 发现 `lease_expires_at <= now`，抛 `Operation start租约无效` → 用户看到「视频交付Operation启动失败」，成片已在 TOS 但 Workspace `deliveries` 未写入。

## 关键文件

- `backend/pixelflow/video_agent/adapters/delivery_operation.py`
- `backend/pixelflow/agent_runtime/jobs/recovery.py`（`OperationStartCoordinator`）
- `backend/pixelflow/agent_runtime/persistence/repositories.py`（`finalize_operation_start_terminal`）
- `backend/pixelflow/video_agent/adapters/scene_package_operation.py`（对照：已用 2h lease）

## 核心逻辑

1. claim start lease → 调 content-app merge（同步阻塞）→ finalize 时校验租约仍有效。
2. 修复：交付默认 `start_lease_duration=1h`，并在 `OperationConflictError` 公开文案中带上冲突细节。
3. 远端对照：同一秒 content-app 日志有 merge 成功 URL，agent 侧报 Operation 启动失败。

## 注意事项

- 不要把「启动失败」当成业务合并失败去改 merge 参数；先查租约与 content-app 是否已成功。
- 租约过期后 TOS 上可能已有成片，需用户重试（新 attempt）或人工回填；幂等键可能挡住重复 start。
- 测试服务器 agent 日志文件名可能是 `agent-dev.log` 而非 `agent-prod.log`。
