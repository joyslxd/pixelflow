---
topic: 场景包进度卡假运行与心跳冻结
module: video-agent
date: 2026-08-10
keywords:
  - structure_progress
  - 已等待 0 分 30 秒
  - scene_package_job_resume_failed
  - 任务不存在或已过期
  - failAssetPackageProgressSteps
---
## 结论摘要
「已用时」是前端按 `startedAt` 本地计时；心跳文案依赖轮询 `structure_progress`。网关热重载 / 旧 Job 404 后轮询停止，若不清进度步骤，会表现为：提示任务过期，但执行规划仍「正在执行」、心跳卡在「已等待 0 分 30 秒」。现：404 时 `failAssetPackageProgressSteps`；陈旧 resume（job_id ≠ 当前 pending）静默忽略；结构模型心跳改为独立墙钟 ticker。

## 关键文件
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（resume catch）
- `web/src/features/video-agent/AgentPipelineProgress.tsx`（`failAssetPackageProgressSteps`）
- `backend/pixelflow/generate/scene_packages.py`（heartbeat_loop）

## 核心逻辑
1. Job 内存态；`--reload` / 重启 → 未完成 Job 404
2. 迟到的旧 job resume 不得 `clearPending` 当前活跃任务
3. 心跳按 `time.monotonic()` 累计，不依赖 wait 循环加法

## 注意事项
- 开发改代码触发 reload 时，正在跑的 prepare-scene-packages 会丢，需手动重试
- 看到「任务过期」后若进度仍 running，属旧 bug；刷新生效本修复后应显示 failed
