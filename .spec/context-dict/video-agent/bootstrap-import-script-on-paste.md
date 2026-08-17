---
topic: 成稿粘贴 bootstrap import_script 落库
module: video-agent
date: 2026-08-13
keywords:
  - import_script
  - bootstrap
  - looks_like_complete_shooting_script
  - native_invoke
  - workspace.script
---

## 结论摘要

用户粘贴完整分镜后，模型常在回复里叙述「调用 `import_script`」，却不发原生 Tool Call，
导致 `workspace.payload.script` 一直为 null，UI 停在「正在处理中」。

修复：`NativeVideoAgentInvoker` 在进入 Agent 循环前，若
`looks_like_complete_shooting_script(content)` 且工作区尚无脚本，则经 Registry/Executor
确定性执行 `import_script(markdown=…)`，发布 `agent.tool.*` 事件，刷新 Workspace，
并在 HumanMessage 前注入「已导入、勿重复」系统注记。这是确定性边界，不是关键词固定路径替代 Planner。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`（`_bootstrap_complete_script_import`）
- `backend/pixelflow/video_agent/tools/script.py`（`ImportScriptInput.markdown`）
- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/entrypoint.py`（`looks_like_complete_shooting_script`）
- `backend/tests/test_video_agent_native_invoke.py`

## 核心逻辑

1. 检测成稿 + 无 `script.content` → bootstrap。
2. `markdown` 取 Turn 正文，并去掉 `【本轮指令】` 尾注。
3. `executor.execute_tool_call` 写 patch；失败发 `tool.failed`，不阻塞后续 Agent（但无落库）。
4. 成功后 `get_workspace` 刷新 `runtime_context`，合并 `tool_names`。

## 注意事项

- 勿在 `native_invoke` 顶层 import `entrypoint`（循环依赖）；bootstrap 内惰性导入检测函数。
- 已有脚本时跳过 bootstrap，避免覆盖用户已导入版本。
- 参数名必须是 `markdown`，不是 `script_content`。
- 导入成功后短接 `response_completed`，禁止再把整篇脚本塞进 astream（见 `import-script-bootstrap-stuck-reasoning-chunk.md`）。
- bootstrap 思考开场占用 `reasoning chunk_index=0`，勿改回 1。
