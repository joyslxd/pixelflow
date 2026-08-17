---
topic: 确认生图后进度卡假「正在执行」
module: video-agent
date: 2026-08-13
keywords:
  - generate_scene_assets
  - 确认生图模型
  - 正在执行
  - short-circuit
  - OpenAI 500
  - 热重载
  - failAssetPackageProgressSteps
---

## 结论摘要

用户看到「3. 生成场景参考图 · 正在执行」长时间空转，常见不是 seeddream 真在排队，而是：

1. FE 模型确认后**乐观**把进度切到生图；
2. Turn 里 bootstrap 启动（或未启动）后仍进模型 astream，网关 LLM 500 / `make dev --reload` 打断延迟提交；
3. 进程内 Job 被热重载清掉；日志无 `seeddream` / `Task … completed`；
4. 进度卡缺少 `generate_scene_assets` 工具事件，永久假转。

修复：`generate_scene_assets` bootstrap 成功/失败均短接 `response_completed`，禁止再进模型；FE 不再乐观切生图阶段，并由「无工具事件 + 思考结束/超时」收口假进度。

## 相关文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/tests/test_video_agent_native_invoke.py`
- `.spec/context-dict/video-agent/model-confirm-bootstrap-and-timeline-cast.md`

## 核心逻辑

1. 解析「确认生图模型 …」→ Registry 直执 → 短接公开回复
2. 进度卡只跟 `nativeAssetsToolSignal`（running/completed/failed）
3. assets 假 running 且无工具事件 → `failAssetPackageProgressSteps`（思考流 streaming 超过 90s 也收口）

## 注意事项

- 排障先看网关：有无 `VideoAgent 延迟提交失败`、有无 seeddream/Task 日志；无供应商日志 = 未真正生图
- 已污染会话：重新选生图模型再确认；刷新后若无图应回到「待选模型」
- 真·串行生图仍可能十几分钟，那时日志应持续有 Task completed；见 `scene-asset-sequential-slow-and-auth-mask.md`
