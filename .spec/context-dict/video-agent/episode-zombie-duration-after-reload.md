---
topic: /episode 显示跑 44 分钟实为热重载僵尸步
module: video-agent
date: 2026-08-10
keywords:
  - 生成剧本正文
  - /episode
  - 44分钟
  - CancelledError
  - stale RUNNING
  - started_at
  - duration_ms
  - 121653e87eaf43b88a05a10aaf093f00
---
## 结论摘要
对话 `121653…` 的「生成剧本正文 /episode」显示约 44 分（2679s），不是 LLM 真跑那么久。08:55:41 开跑后约 08:56:07 后端 `--reload` 关掉进程（确认 HTTP 也 500/`CancelledError`），步骤留在 RUNNING；09:39:31 Snapshot 才陈旧恢复，真实生成约 50 秒。UI 耗时 = `completed_at - 首次 started_at`，把僵尸等待算进去了。

## 关键文件
- `backend/pixelflow/video_agent/executor/service.py`（陈旧重跑传新 `now`）
- `backend/pixelflow/video_agent/workspace/repository.py`（RUNNING 重入时刷新 `started_at`）
- `.spec/context-dict/video-agent/plan-ack-order-and-characters-stall.md`

## 核心逻辑
1. 开发热重载会取消进行中的 Skill LLM，留下 RUNNING 僵尸
2. 刷新 Snapshot → `maybe_resume_stale_running_plan`（超时 180+30s）重跑
3. 修复后：陈旧重跑刷新 `started_at`，耗时只计真实重跑窗口

## 注意事项
- 该会话 plan 已 `completed`；若页面仍「卡住」先重新登录（曾出现 snapshot 403 token_expired）再刷新
- 开发期改代码触发 reload 时，正在跑的脚本步可能要等刷新才恢复
- 勿把墙钟耗时直接当成模型延迟
