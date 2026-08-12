---
topic: 入场思考流完成但前端无返回
module: video-agent
date: 2026-08-12
keywords:
  - 入场思考流完成
  - continue_images
  - 没有参考图，直接生成
  - AGENT_THINKING_COMPLETED
  - OperationCompletionDispatchError
  - scene_packages 回填
---

## 结论摘要

日志 `入场思考流完成 … intent=continue_images` **只表示 Intake LLM 结束**，不是整轮 Turn 结束，也不是用户可见「答案气泡」已落库。随后还应：收口 `AGENT_THINKING_COMPLETED` → 裁决 Plan → 发布 plan 事件 → 收尾 Turn。

本轮卡住常见组合：
1. `completion_dispatch` 反复失败 → 资产包未写入 `VideoWorkspace`，前端卡却可能已有；
2. 参考图闸门要求 `has_scene_packages`，未满足则落到 Planner，前端长时间「正在处理中」；
3. `publisher.complete()` 若写事件卡住，caret 永不收口（日志已在 complete 之前打印）。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/agent_runtime/jobs/completion.py`
- `backend/pixelflow/video_agent/operation_resume.py`

## 核心逻辑

1. Intake `continue_images` / `generate_scene_assets`：有包 → 立即 `generate_scene_assets`；无包 → `WAITING_FOR_INPUT`，禁止静默 Planner
2. 有 `scene_package_job.job_id` 且工作区无包时，从完成事件 `result` 回填 `scene_packages`/`global_assets`
3. `complete()` 8s 超时，避免收口写事件拖死整轮
4. resume 失败额外打 `error_type=`，仍不回显内容

## 注意事项

- 周期 `OperationCompletionDispatchError` 多半是脏完成事件与 Plan 步骤状态对不上；与「思考完成」日志不同线
- 前端「正在处理中」来自 Turn 未收尾 / runtimeNotice，不是思考流正文
