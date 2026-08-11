---
topic: 思考流去掉固定「已收到创作请求」占位句
module: video-agent
date: 2026-08-11
keywords:
  - INTAKE_THINKING_PREAMBLE
  - optimisticAgentThinking
  - reasoning
  - 流式思考
---

## 结论摘要

「已收到创作请求，正在结合上下文整理入口判断…」不是业务必需：前端乐观思考卡与后端 `push_delta` preamble 曾重复注入同一句。已删除；思考正文只展示模型 `reasoning` 流。仍保留空壳（title/subtitle + `status=streaming`）覆盖 turns/start 等待期。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/thinking_stream.py`
- `backend/tests/test_video_agent_thinking_stream.py`

## 核心逻辑

1. 发送时 `text: ""` 乐观壳 → SSE `agent.thinking` 到来后替换
2. `stream_intake_thinking` 只 `start` + LLM reasoning/answer，不再固定首句
3. 无 reasoning 时思考区可空，answer 仍进气泡

## 注意事项

- 历史方案锚点仍匹配「已收到创作请求」气泡文案，与思考区无关
- 首 token 前可能短暂空白 Thought，属预期
---
