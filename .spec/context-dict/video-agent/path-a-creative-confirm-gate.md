---
topic: Path A /start 后选题创意确认闸门
module: video-agent
date: 2026-08-11
keywords:
  - confirm_script_creative
  - 确认选题创意
  - 同意创意继续
  - 换个方向
  - isAgreeScriptCreativeRequest
  - fuzzy brief
  - Path A
  - 视频画幅
  - 结尾行动引导
  - 还需要你确认
---
## 结论摘要
模糊主题走 Path A 创作时，`/start` 完成后插入 `confirm_script_creative` 确认闸门；用户同意后才继续 `/plan` 及后续阶段。不满意可用自然语言改方向：取消当前确认计划并开新 Turn 重跑 `/start`（卡片流程从头开始）。

若用户原文（`latest_input`）缺画幅或结尾行动引导：确认卡 `cost_summary` 追加「还需要你确认」追问（已识别时长会展示，如 180秒）；前端与工具执行都会挡住「同意创意继续」，直到用户在对话框补齐。不以 `/start` 产物里臆造的默认画幅为准。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`（create 计划在 start 后插确认步；画幅/CTA 短回复算创意跟进）
- `backend/pixelflow/video_agent/production_fields.py`（时长/画幅/CTA 探测与追问文案）
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`（`ConfirmScriptCreativeTool` 缺字段拒绝 + start 禁止臆造画幅/CTA）
- `backend/pixelflow/agent_runtime/service.py`（确认卡费用摘要含创意预览与缺字段追问）
- `backend/pixelflow/video_agent/workspace/repository.py`（脚本 Skill 计划识别含确认工具）
- `web/src/features/video-agent/scriptSkillStages.ts` + `LegacyWorkspace.tsx` + `AgentConfirmationCard.tsx`

## 核心逻辑
1. Path A：`start → confirm_script_creative(confirmation_required) → plan…export`
2. 确认投影：标题「确认选题创意」；按钮「同意创意继续 / 换个方向」；摘要带 start 创意预览
3. 缺字段：`missing_creative_production_fields(latest_input)` → 摘要含 `还需要你确认`；FE `creativeConfirmNeedsClarification` 拦截同意；工具 execute 再校验
4. NL：同意短句 → confirm API（缺字段则提示先补）；「换个方向/取消」→ cancel 并提示补充；其它文本（含「9:16，引导进直播间」）→ cancel 后新 Turn 重跑 start
5. `cancel_active_script_skill_plans` 仍能识别带确认步的 Path A 计划
6. Executor 计划步数上限放宽到 9（Path A = 8 个 Skill + 1 个创意确认）

## 注意事项
- Path B 成稿润色（review→export）不加此闸门
- LLM Planner 提案仍限制最多 8 步；仅入口种子的 Path A 会到 9 步
- 改创意依赖 `latest_input` 变化改 fingerprint，才会真正重生成 start
- Snapshot 不回传 tool_name，前端用步骤标题识别闸门
- 同意后确认 HTTP 只完成确认步，后续由后台 resume；详见 `confirm-http-blocks-followup.md`
- 画幅/CTA 以用户原文为准，不要用 start Markdown 里可能被模型填的 9:16 当作已确认
