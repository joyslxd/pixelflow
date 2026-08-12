---
topic: 确认脚本后思考流 revision 冲突
module: video-agent
date: 2026-08-12
keywords:
  - AgentRuntimeRecordConflictError
  - expected_revision
  - confirm_for_generation
  - _apply_workspace_patch_resilient
  - 延迟提交
  - 确认脚本并生成资产包
---

## 结论摘要

第二轮「确认脚本」会先 `saveVideoAgentScript(confirm_for_generation=true)` bump workspace revision，再开 Turn 跑长思考流。思考结束后若仍用思考前的 `expected_revision` 写 `latest_input`，会报 `VideoAgent workspace revision 已变化`，延迟提交整 Turn 失败。修复：思考后重读 workspace，并用最多 3 次冲突重试写 patch。

## 相关文件

- `backend/pixelflow/video_agent/entrypoint.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（先确认再 Turn）
- `backend/tests/test_video_agent_entrypoint.py`

## 核心逻辑

1. FE：`confirmScriptPlanAndGenerateAssetPackage` → save script + confirm → `handleSupervisorTurn`
2. BE：`create_workspace` 取旧 revision → `stream_intake_thinking` → `_submit_turn_after_thinking`
3. 思考期间确认/旧执行器可能已 revision++
4. `_submit_turn_after_thinking` 开头 `get_workspace` 重读；所有 patch 走 `_apply_workspace_patch_resilient`

## 注意事项

- Operation 恢复 `completion_dispatch` WARNING 是另一条线，与本次确认冲突无关
- 重试是「重读后覆盖写本轮 patch」，不合并冲突字段的精细语义
