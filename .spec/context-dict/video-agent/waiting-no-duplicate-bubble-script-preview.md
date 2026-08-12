---
topic: waiting 追问不写气泡；intake_draft 必须可预览
module: video-agent
date: 2026-08-11
keywords:
  - waiting_for_input
  - thinking-answer
  - intake_draft
  - artifact_ref
  - AgentScriptPreviewPanel
  - projectScript
---

## 结论摘要

缺字段追问时，`user_message` 只进执行方案卡 `public_goal`，思考流 **不写 answer channel**，前端也不在 `waiting_for_input` 时落 `thinking-answer` 气泡，避免卡+气泡同一段话。

`intake_draft` 种子必须带 `artifact_ref`；前端 `projectScript` 对缺 ref 的草稿合成稳定引用。否则 Snapshot 有 script、右侧预览却整块消失，用户无法确认。

## 相关文件

- `backend/pixelflow/video_agent/thinking_stream.py`
- `backend/pixelflow/video_agent/entrypoint.py`（`_seed_script_draft_payload`）
- `web/src/features/video-agent/state/workspace.ts`
- `web/src/features/video-agent/AgentPlanTimeline.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑

1. `needs_user_reply` → 跳过 `push_delta(..., channel=answer)`
2. FE：`plan.status === waiting_for_input` → 不 `appendPersistedSupervisorNotice`
3. Timeline waiting 正文只提示补充，不重复粘贴 `publicGoal`
4. 有脚本草稿时**不**自动打开右侧预览；需点击对话内版本链接或执行方案步骤

## 注意事项

- 历史会话若 script 无 `artifact_ref`，下一轮 field_followup/seed 会补齐；仅靠 FE 投影合成也能立刻看见
- Path B 确认仍看 `workspaceHasExportReady`（成熟分镜稿可无 export 阶段）
- waiting 正文现会展示 `publicGoal`（追问内容），避免只剩「等待补充」空壳；仍不写 thinking-answer 气泡
- 工作区已有画幅/CTA 时 Entrypoint 会 reconcile 掉 Intake 误报 missing，见 `thinking-stuck-preamble-caret.md`
