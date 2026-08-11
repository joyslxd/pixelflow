---
topic: Skill 命令流 Plan 与脚本进度
module: video-agent
date: 2026-08-07
keywords:
  - run_script_skill_stage
  - /start /plan /characters /outline /episode /review /compliance /export
  - agent.step.progressed
  - ValidationError
---
## 结论摘要
视频意图后首 Plan 改为 sedance Skill 八阶段：`start→plan→characters→outline→episode→review→compliance→export`，工具 `run_script_skill_stage`。阶段进度发 `agent.step.progressed`，执行器在推送后 `asyncio.sleep(0.05)` 让出事件循环给 SSE。旧 `brainstorm_script` 路径：完整 `latest_input` 入模；Brief `ValidationError` 先故事感知 Markdown 再模板降级。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/executor/service.py`
- `backend/pixelflow/video_agent/adapters/video_domain.py`
- `web/src/features/video-agent/AgentPlanTimeline.tsx`

## 核心逻辑
1. `_should_seed_script_draft` 为真时落 8 步 Skill Plan，不再用 inspect+brainstorm。
2. 每阶段读 `workspace.latest_input` + 上游 `script_pipeline`，episode/export 写 `script` 供右侧预览。
3. progressed 必须在长 LLM 调用前发出，并 yield，否则前端只看到最终完成。
4. `/characters` 产出角色+场景+道具三设定集；export 终稿必须含这三块。

## 注意事项
- 八阶段会串行调用模型，耗时显著；后续可对广告场景压缩阶段或加确认闸门。
- Skill 原文要求“每步确认”，当前 P0 自动连跑；Path A 仅在 `/start` 后开「确认选题创意」闸门（`confirm_script_creative`），详见 `path-a-creative-confirm-gate.md`。
- 旧会话仍可能是两步 brainstorm Plan，需新建对话验证。
- 旧终稿不会自动补场景/道具，需新对话重跑流水线。
- 开发热重载时若 SSE（agent-events）未关闭，uvicorn 会卡在 Waiting for connections to close，导致 conversations/capability 全部 pending；`run.py` 已设 `timeout_graceful_shutdown=5`。
