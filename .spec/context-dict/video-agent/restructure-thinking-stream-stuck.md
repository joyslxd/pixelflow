---
topic: 重新拆解思考流假死（Tool 未接通 report_thinking）
module: video-agent
date: 2026-08-14
keywords:
  - 重新拆解脚本
  - 思考中卡住
  - report_thinking
  - report_progress
  - import_script
  - reasoning_chunk_seq
---

## 结论摘要

「重新拆解脚本」UI 只显示开场「正在处理…」后假死，不是模型没跑，而是：

1. 开场 `reasoning_summary.delta(chunk=0)` 之后，Agent 可能长时间无 reasoning channel；
2. 真正耗时的是 `import_script` → `extract_imported_script_structure`（现含 bgrs，可达数分钟）；
3. Gateway / failsafe 构造的 `VideoToolContext` **以前没有** `report_progress` / `report_thinking`，`emit_progress` 与 `on_token=emit_thinking_delta` 全部静默跳过。

修复：`_install_tool_stream_reporters` 把回调写入 `runtime_context`；Gateway `build_context` 透传；failsafe/成稿 bootstrap 复用；与 astream 共用 `reasoning_chunk_seq` 避免 event_id 冲突。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/tool_gateway.py`
- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/tools/registry.py`

## 核心逻辑

1. 入模前 install reporters（若已 open 则 start_chunk=0）
2. Tool 阶段文案 → progress → reasoning delta
3. 拆解/阶段生成：**不要**把正文 token 接 `emit_thinking_delta`；用里程碑 + 心跳进度（见 `import-structure-thinking-no-body.md`）
4. failsafe 再 install(start=80) 并注入同一回调到 VideoToolContext

## 注意事项

- 长拆解仍可能要 1–3 分钟，但应持续出现短进度（「正在整理角色设定…」「拆解仍在进行…」），而不是只停在开场句，更不要灌全文
- 勿再让 Gateway 构造无回调的裸 Context
- chunk 序号必须与模型流共享，否则会 `AgentRuntimeRecordConflictError` 把后续思考写库失败
