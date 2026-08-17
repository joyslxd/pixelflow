---
topic: Turn 展示顺序 Thought → Activity → 结论
module: video-agent
date: 2026-08-13
keywords:
  - AgentTurnGroup
  - reasoning_summary
  - bootstrap import
  - 正在处理中
  - showReasoningPlaceholder
---

## 结论摘要

用户看到「活动（导入完整脚本）」先于思考流，底部再叠「正在处理中」。期望顺序是：
流式 Thought → 活动 → 结论气泡。

根因：bootstrap `import_script` 先发 `agent.tool.*`，未先发 `reasoning_summary`；
且 Turn 组可见忙碌时仍显示 runtimeNotice「正在处理中」，看起来像错误的结论层。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`（`_emit_bootstrap_reasoning_open`）
- `web/src/features/native-video-agent/chat/AgentTurnGroup.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（抑制重复 working notice）

## 核心逻辑

1. Bootstrap 工具前先 `reasoning_summary.delta`，再 `tool.started`。
2. UI：无思考但有活动时先占位「思考中…」。
3. `video_agent_v2` 下 Turn 已展示忙碌时隐藏 tone=working 的 runtimeNotice。

## 注意事项

- Bootstrap 不主动 `reasoning_summary.completed`，留给后续模型流续写/收口。
- 结论气泡仍只来自 `agent.response.*`，不要把 runtimeNotice 当结论。
