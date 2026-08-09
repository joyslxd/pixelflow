---
topic: 场景包轮询遇 503/断网应保留 pending
module: video-agent
date: 2026-08-09
keywords:
  - auth_service_unavailable
  - generate-scene-assets
  - retain_pending
  - scene_package_job_resume_failed
  - 503
---
## 结论摘要
轮询 `generate-scene-assets/jobs/{id}` 时若 content-app 认证短暂不可用（断网 → 503 `auth_service_unavailable`），旧逻辑会 `clearPendingScenePackageJob`，Agent 无法自动续跑。现改为瞬时错误 `retain_pending` + 指数退避重试；仅 404/明确失败才清 pending。

## 关键文件
- `web/src/lib/scenePackageJobResume.ts`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（`resumePendingScenePackageJob` catch）

## 核心逻辑
1. `classifyScenePackageJobResume`：503/502/504/408/网络文案 → retain；404 → clear_not_found
2. retain 时保留 `pendingScenePackageJobRef`，提示后 `setTimeout` 再 `resumePendingScenePackageJob`
3. 后端 job 若仍在内存且未过期，恢复后可继续拿到结果

## 注意事项
- 若网关进程重启，内存 job 会丢失，只能从模型选择卡/场景包卡重新发起
- 本次已清掉 pending 的会话，刷新不会自动续；需手动再确认模型或重试参考图
