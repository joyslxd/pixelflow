---
topic: Intake 思考流必须 HTTP stream=true
module: video-agent
date: 2026-08-12
keywords:
  - stream=true
  - THINKING_PREAMBLE_TIMEOUT_SEC
  - stream_chat_tokens
  - astream
  - streaming=False
  - LangChain
  - reasoning_content
---

## 结论摘要

入场 `stream_intake_thinking` 必须走 `astream(..., stream=True)`。LangChain 在实例 `streaming=False` 时会把 `astream` 退化成 `ainvoke`，上游日志会出现 `stream: false`，整段返回前无增量，易触发 wait_for 超时。`request_timeout` 不得短于 preamble wait_for。

同 Turn 里若仍见 `stream: false`：多半是旧 Planner `with_structured_output.ainvoke`。Planner 已改为 `stream_chat_tokens` + JSON 解析。历史「创意 DTO 生成器」属于 plan_llm 非 VideoAgent 路径。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`
- `backend/pixelflow/video_agent/planner/model.py`
- `backend/tests/test_video_agent_thinking_stream.py`
- `backend/tests/test_video_agent_planner.py`

## 核心逻辑

1. `_create_streaming_chat_model` 传 `streaming=True`，创建后 `setattr(model, "streaming", True)`
2. `stream_chat_tokens` 显式 `astream(..., stream=True)`；签名不支持时回退无 kwargs
3. `DeepSeekVideoPlanningModel.propose` 同样走 `stream_chat_tokens`，禁止 `structured.ainvoke`
4. `THINKING_PREAMBLE_TIMEOUT_SEC` 与 `THINKING_REQUEST_TIMEOUT_SEC` 对齐为 90s
5. 原始 `reasoning_content` 仍不进公开 reasoning（防复述系统提示）；用户可见进度只靠 NDJSON `progress`

## 注意事项

- 验证时区分 Intake / Planner / plan_llm 三条请求，不要混看
- 验证看上游 HTTP `stream` 字段，不只看本地构造参数
