---
topic: 思考流 UI 去重：一行头 + reasoning
module: video-agent
date: 2026-08-11
keywords:
  - AgentThinkingStream
  - Thought for
  - Thinking…
  - statusLine
  - title subtitle
---

## 结论摘要

用户看到的「Thought for 3s / Thinking… / 正在结合上下文… / Thinking」是前端叠了四层壳，不是模型写了四遍。已改为：空流式只显示「思考中…」；有正文后显示 `Thought for Xs` + reasoning；不再渲染 title/subtitle 灰行和底部 Thinking。后端 started 事件 title 收敛为「思考中」。

## 相关文件

- `web/src/features/video-agent/AgentThinkingStream.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（乐观壳）
- `backend/pixelflow/video_agent/thinking_stream.py`
- `web/src/lib/supervisor/reducer.ts` / `workspaceProjection.ts`

## 核心逻辑

1. 可见层只认 header + `thinking.text`
2. `title`/`subtitle` 仅事件/Snapshot 元数据
3. 首 token 前无空正文占位行

## 注意事项

- 若仍觉得「思考正文本身」太长，那是模型 reasoning，需另调 prompt / reasoning_effort
- 历史事件若仍带长 title，也不会再显示在 UI
