---
topic: 口述 merge_videos 却不执行合并
module: video-agent
date: 2026-08-15
keywords:
  - compose_or_export_video
  - merge_videos
  - VideoToolCommitmentMiddleware
  - 合并视频
  - confirmation_required
  - creation_contract
---

## 结论摘要

用户说「合并视频吧」时，思考里写「调用 merge_videos」，但活动区仍停在上一轮「分镜视频」、无合并执行。根因：

1. **没有 `merge_videos` Tool**；真实已注册名是 `compose_or_export_video(output_type="mp4")`。
2. 思考模型**只口述不发原生 `tool_calls`** → ReAct 不进 tools，Plan 不更新。
3. 确认闸门命中时原先不 `note_business_tool`，UI Plan 易残留旧「分镜视频」。
4. 合并 Adapter 只读 `video_params`，缺省时未回退 `creation_contract`。

修复：commitment 把 `merge_videos` /「合并视频」口述映射为 `compose_or_export_video`+`mp4`；提示词显式路由；确认闸门也写观察 Plan；合并参数回退创作合同；单版本就绪 variant 可交付。

## 相关文件

- `backend/pixelflow/video_agent/middleware/tool_commitment.py`
- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/tools/delivery.py`
- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/pixelflow/video_agent/adapters/delivery_operation.py`

## 注意事项

- 合并仍是计费 Tool，`confirmation_required=True`：强制 Call 后会先出确认卡，不会静默扣费
- **禁止**对 `compose_or_export_video` 做确定性 bootstrap；合并必须走 ReAct
- 重启 Gateway 后 commitment 才生效
- 勿再注册假名 `merge_videos`；别名只存在于 commitment / 提示词纠正
- Plan 空壳「规划中」见 `merge-plan-title-and-confirmation-step.md`
