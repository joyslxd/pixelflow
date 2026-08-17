---
topic: 思考口述 Tool 却未执行（ReAct 假规划）
module: video-agent
date: 2026-08-15
keywords:
  - inspect_video_workspace
  - VideoToolCommitmentMiddleware
  - tool_calls
  - reasoning_content
  - digest
  - scene_videos_ready_count
  - ReAct
---

## 结论摘要

用户看到思考写「让我调用 inspect_video_workspace」，但活动区无 Tool、无结果。根因不是 Gateway 卡死，而是：

1. **思考模型**把计划写进 `reasoning_content`，**未发原生 `tool_calls`** → LangGraph 不进 tools 节点，ProgressMiddleware 不发 `agent.tool.*`。
2. 即便真调了旧版 `inspect_video_workspace`，摘要也只有「分镜 N 个」，**没有成片就绪/轮询计数**；digest 原先也只有 `scene_count`。

修复：

- digest / inspect 增加 `scene_videos_ready_count|polling|failed|idle` 与可选 per-scene state
- `VideoToolCommitmentMiddleware`：口述白名单只读 Tool（目前仅 inspect）却无 tool_calls 时强制补发
- 系统提示强调：有 digest 视频字段可直接答；要调 Tool 必须原生 Call

## 相关文件

- `backend/pixelflow/video_agent/workspace/digest.py`
- `backend/pixelflow/video_agent/tools/inspect_workspace.py`
- `backend/pixelflow/video_agent/middleware/tool_commitment.py`
- `backend/pixelflow/video_agent/agent.py`
- `backend/pixelflow/video_agent/prompts.py`
- `backend/tests/test_video_agent_tool_commitment.py`

## 注意事项

- 只强制只读 Tool，禁止自动补发 `generate_scenes` 等计费动作
- 重启 Gateway 后装配才含新 Middleware
- 成片仍依赖 Operation 回写 `video_url`；digest 只反映 Workspace 事实
- `inspect_workspace` 对 `workspace.digest` 必须延迟导入，否则 `tools ↔ workspace.repository ↔ executor` 循环导入
