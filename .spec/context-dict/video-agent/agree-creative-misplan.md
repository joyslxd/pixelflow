---
topic: 同意创作误跑 import+confirm 与假排队
module: video-agent
date: 2026-08-11
keywords:
  - 同意创作
  - confirm_script_creative
  - 工具参数无效
  - script_plan_confirmed
  - 上一条任务还在执行
  - isAgreeScriptCreativeRequest
---

## 结论摘要

用户补齐生产字段后回「同意创作」时：前端原先不认「同意创作」（只认「同意创意」），会新开 Turn；后端有待确认时仍把确认口令交给 Planner，再排 `import_script + confirm_script_creative`。`confirm_script_creative` 只认 Path A 的 `script_pipeline.start`，导入脚本路径没有 start → `VideoToolValidationError` 被 Registry 吞成「工具参数无效」。同时执行器思考流未收尾，Thought 计时长、Turn 易假排队。

## 相关文件

- `web/src/features/video-agent/scriptSkillStages.ts`（`isAgreeScriptCreativeRequest`）
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/tools/registry.py`
- `backend/pixelflow/video_agent/executor/service.py`
- `.spec/context-dict/video-agent/false-queue-stale-turn.md`

## 核心逻辑

1. FE：`同意创作` / `同意创意` 走确认卡 API
2. 有 blocking + 确认口令 → inspect 提示点确认卡，禁止 Planner 重排
3. 无 blocking、字段已齐、确认口令 → 直接 `script_plan_confirmed=True`
4. `confirm_script_creative` 无 start 时回退 `workspace.script`；缺项优先读 `missing_requirements`
5. Registry 透出业务校验文案；Executor `finally` 里 `thinking.complete()`

## 注意事项

- 「继续生成视频」等成片口令不要走脚本确认短链路（需排除 `_is_continue_video_generation`）
- 假排队仍可能来自更早僵尸 Turn，见 `false-queue-stale-turn.md`
