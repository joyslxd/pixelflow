---
topic: 公开回答禁止泄漏伪 Tool Call XML/JSON
module: video-agent
date: 2026-08-13
keywords:
  - tool_call
  - update_script_content
  - import_script
  - response.delta
  - strip_tool_markup
---

## 结论摘要

用户贴长分镜时，模型曾虚构未注册工具 `update_script_content`，把 `<tool_call>…JSON`
写进 `content` 流；`NativeVideoAgentInvoker` 原样推 `agent.response.delta`，回答卡卡在
半截 `"ty`。正确录入工具是 `import_script`。

现已：按模型轮次隔离；遇到原生 tool_calls 或伪 markup 即停止向公开回答灌流；
`response.completed` 用剥离后的最终正文盖住；系统提示禁止正文伪 Tool Call。

## 相关文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/tools/script.py`（`import_script`）
- `backend/tests/test_video_agent_native_invoke.py`

## 核心逻辑

1. `tool_markup_cut_index` / `strip_tool_markup` 切断 `<tool_call` / `{"tool_name"` 等
2. `on_chat_model_stream`：有 tool_call_chunks 或 markup 则 `gen_blocked`
3. `on_chat_model_end`：无 tool 才提交 `public_response`，然后 `_reset_generation`
4. `finally` 必发 `response.completed`，前端用 completed 覆盖半截 delta

## 注意事项

- 仓库没有 `update_script_content`；完整脚本路径是 `import_script`
- 若模型只吐伪 Tool Call、无用户可读前缀，公开回答会落到「已完成本轮处理」
- 真正的绑定 Tool Call 仍走 Gateway/活动时间线，不进回答卡
