---
topic: 重新生成视频时看板显示生成完成且工作台无反应
module: agent-runtime
date: 2026-09-02
keywords:
  - suspended_operation
  - generate_scenes
  - scene_videos_polling_count
  - generation_jobs
  - 重新生成中
---
## 结论摘要

对话 `2b29e4ec...` 再次确认生成后，Gateway Job 已是 `polling` 且 Content-App start 返回 200，模型已是 `seedance-2.5`。前端却显示「等待生成任务完成 · 生成完成 2 项」。根因不是任务没跑，而是公开 digest 把镜头里的历史 `failed` 任务排在当前 `queued` 之前，整镜被标成失败；看板进度只统计参考图成功数；挂起后首次 Workspace 回读若判定无进行中任务就停止轮询。

## 关键文件

- `backend/pixelflow/video/workspace/digest.py`
- `backend/pixelflow/generation_jobs/projector.py`
- `web/src/features/agent-runtime/workspaceV2.ts`
- `web/src/features/agent-runtime/useAgentConversation.ts`

## 核心逻辑

1. 每镜只认最后一条 GenerationJob；`queued/starting` 视为进行中。
2. digest 投影 `generation_jobs` 与 `scene_videos_polling_count`，Prompt Package 映射 `generating`。
3. 看板文案优先分镜视频进行中，不能被已完成参考图写成「生成完成」。
4. `suspended_operation` 期间持续回读 Workspace，直到 Run 离开挂起。

## 注意事项

- Worker 在 polling 阶段不会回写 Workspace 任务状态，镜头记录可能仍是 `queued`。
- 同一镜多次重试会堆积旧失败 Job；成功投影必须按当前 `plan_step_id` 批次，不能要求历史全部 succeeded。
- 刷新页面即可看到修复后的看板；正在跑的 Provider 任务不用重提。
