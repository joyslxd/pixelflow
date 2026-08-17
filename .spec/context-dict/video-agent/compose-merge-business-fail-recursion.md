---
topic: compose 已执行但合并业务失败并撞 GraphRecursion
module: video-agent
date: 2026-08-17
keywords:
  - compose_or_export_video
  - ContentAppMergeJobService
  - GraphRecursionError
  - 视频交付合并失败
  - success false
---

## 结论摘要

重启后「重新发起合并」已能越过 FORCED STOP 真正进 `compose_or_export_video`。
前端「compose … 执行失败」对应 content-app **同步** `/api/video/merge` 跑了数分钟后
业务失败（`success:false` → Operation `FAILED`），不是再卡在确认闸门。
失败原因原先被 Registry 吞成笼统文案且不打日志；Agent 失败后继续盲重试直至
`GraphRecursionError: Recursion limit of 25`。

## 关键文件

- `backend/pixelflow/skills/borgrise/provider_jobs.py`（ContentAppMergeJobService）
- `backend/pixelflow/video_agent/adapters/delivery_operation.py`
- `backend/pixelflow/video_agent/tools/registry.py`
- `backend/pixelflow/video_agent/middleware/tool_gateway.py`
- 后端日志：`GraphRecursionError`；此前无 `video tool execution failed` 因 soft-fail

## 核心逻辑

1. 合并是同步终态：start 内 HTTP 可能长达数分钟；`success:false` → FAILED。
2. 公开文案应带「视频交付…」前缀；Registry 对交付类 detail 放行。
3. soft-fail / 确认后同轮禁止再调同一 Tool，避免撞递归上限。

## 注意事项

- 再试合并时看日志是否出现 `content-app video merge business failed`。
- 14 镜合并失败常见：额度、供应商业务错误、个别 URL 不可达；需对照 content-app。
