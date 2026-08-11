---
topic: 参考图生成「卡住」实为串行慢 + 轮询鉴权失败
module: video-agent / generate-scene-assets
date: 2026-08-10
keywords: scene_assets, seeddream, sequential, poll 403, token_expired, asset_progress
---
## 结论摘要
场景参考图 Job（`/generate-scene-assets`）对素材是**串行**调用 seeddream（`for` 循环 + `asyncio.to_thread(reference_image)`），8 张图常见要十几到二十多分钟，不像卡死。供应商 task 全部 `completed` 后 Job 只写内存 `_SCENE_ASSET_JOBS`；若前端轮询此时拿到 **403/401（token 过期）**，轮询中断，UI 会一直停在「生成中」，且**完成结果进不了工作区**（结果未落库，靠轮询回写）。

## 关键文件
- `backend/pixelflow/generate/scene_assets.py`（串行 `asset_jobs`）
- `backend/app/gateway/routers/pixelflow_video.py`（`_SCENE_ASSET_JOBS` / `_run_scene_asset_job`）
- `backend/pixelflow/skills/borgrise/run_generation.py`（`IMAGE_POLL_TIMEOUT=600`，单张最多约 10 分钟）
- `web/src/lib/api.ts`（`pollSceneAssetsJob`，3s 间隔）

## 核心逻辑
1. 每张图：`POST multi_reference_image_generation` → `poll_task` 打出 `Task …: completed`
2. `on_progress` 回写 `asset_progress.completed/total`
3. 全部结束后 `status=completed`，仅内存；FE 收到后才 `apply` 到工作区
4. `--reload` 或进程重启会丢掉未取走的 Job

## 注意事项
- 排障先看网关日志：是否持续出现新的 `Model: seeddream` / `Task …: completed`，以及 job 轮询是否从 200 变成 403/401
- 鉴权失败时：重新登录后再看；若 Job 已丢，只能重新点生成
- 不要用「总耗时很长」直接判卡死；先对一下 `completed/total` 与供应商 task 进度
