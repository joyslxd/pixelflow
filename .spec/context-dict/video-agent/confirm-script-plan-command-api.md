---
topic: 确认脚本独立命令 API
module: video-agent
date: 2026-08-13
keywords:
  - confirm-script-plan
  - VideoAgentConfirmScriptPlanRequest
  - script_plan_confirmed
  - prepare_scene_packages
  - bootstrap fallback
---

## 结论摘要

按钮确认脚本（右侧确认 / 同意方案）走  
`POST /agent/conversations/{id}/video-agent/commands/confirm-script-plan`，  
一次写入确认位（可选带 markdown）并启动 `prepare_scene_packages`。  
**禁止**再伪造 Turn `content: "确认脚本"` 靠 marker bootstrap。  
自然语言「确认脚本」仍进 Native Agent；能否 prepare 由 Tool 前置条件裁决。

## 相关文件

- `backend/pixelflow/agent_runtime/service.py`（`confirm_video_agent_script_plan`）
- `backend/app/gateway/routers/pixelflow_conversations.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`（确认位 + 生产字段前置）
- `backend/pixelflow/video_agent/native_invoke.py`（已删确认话术 bootstrap）
- `web/src/lib/supervisor/api.ts` / `LegacyWorkspace.tsx`

## 核心逻辑

1. CAS `expected_revision`；可选 markdown → save(confirm)
2. `reconcile_missing_with_workspace` 非空 → 422 `video_agent_script_not_ready`
3. Executor 执行 `prepare_scene_packages`；返回 `job_id` + Snapshot 可刷新
4. 补字段 bootstrap 仅作 fallback 短接；确认脚本短令不进补字段门闩

## 注意事项

- Tool 未确认或仍缺画幅/CTA 时 `VideoToolValidationError`，防 NL 误调
- 409 仍带回 `current_revision`；FE 最多重试 3 次
- 无参考图 / 生图模型卡仍可能有 bootstrap，不在本命令范围内
