---
topic: 成稿粘贴 bootstrap 卡住（思考事件 chunk 冲突 + 导入后再进模型）
module: video-agent
date: 2026-08-13
keywords:
  - import_script
  - bootstrap
  - reasoning_summary_delta
  - chunk_index
  - AgentRuntimeRecordConflictError
  - 思考中卡住
  - extract_imported_script_structure
---

## 结论摘要

用户粘贴完整拍摄脚本后，UI 显示「检测到完整拍摄脚本…」随后长时间「思考中」假死。根因有两层：

1. `_emit_bootstrap_reasoning_open` 曾用 `chunk_index=1` 发思考事件；`_invoke_streaming` 首包也从 1 起，同 turn 确定性 `event_id` 冲突 → `AgentRuntimeRecordConflictError`，思考/回答事件写库失败。
2. 导入（含最长 180s 的结构化拆解）成功后仍把整篇脚本塞进原生 Agent astream，二次模型调用慢且易与 bootstrap 事件抢 ID。

修复：bootstrap 占用 `chunk_index=0`；导入阶段接通 `report_progress`；`import_script` 成功后与补字段一样短接 `response_completed`，不再进模型。

## 相关文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/events/publisher.py`
- `backend/pixelflow/video_agent/tools/script.py`
- `backend/tests/test_video_agent_native_invoke.py`

## 核心逻辑

1. Native 事件 ID：`uuid5(kind + conversation + turn + chunk_index)`，同 turn 同 chunk 必冲突。
2. 成稿检测 → bootstrap `import_script` → 阶段 progress 推思考流 → 写 workspace → 短接公开回复。
3. 缺画幅/CTA 时回复里明确「请补充…」；齐全则引导右侧预览确认。

## 注意事项

- 其它仍会进模型的 bootstrap（无参考图续跑、确认生图模型）也依赖 open=0 / stream 从 1 起；勿再把 open 改回 1。
- 结构化拆解超时 `SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS=180`；长成稿仍可能等几分钟，但应有阶段文案而非假死。
- 日志里大量无关的 `completion_dispatch` / `AgentRuntimeRecordConflictError` 是 Operation 恢复噪音，勿与本 turn 思考事件冲突混淆；本 turn 特征是 `原生 Agent 流式事件发布失败` + `reasoning_summary_delta`。
