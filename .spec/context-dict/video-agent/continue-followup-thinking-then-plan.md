---
topic: 下一步跟进必须先思考再规划
module: video-agent
date: 2026-08-11
keywords:
  - 继续资产
  - 开始生图
  - turns/start
  - 无断点
  - intake_draft
  - 思考流
  - INTAKE_PLAN
  - prepare_scene_packages
---

## 结论摘要

用户补齐画幅/CTA 后说「继续资产吧 / 开始生图」时，前端曾因无可恢复闸门硬提示「当前没有可自动恢复的断点」，把编排截断；即便进 Agent 也会因追问回合未种子脚本而 inspect「脚本 0 份」并规划超时。

现改为：`video_agent_v2` **不再做前端关键词断点恢复**；自然语言一律 `turns/start` → 思考流解读并产出 steps（如 `prepare_scene_packages`）→ 落 Plan。缺字段追问时把成稿种子写入 `workspace.script`（`source=intake_draft`）。

## 相关文件

- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/entrypoint.py`（`_seed_script_draft_payload` / `_plan_from_intake_steps`）
- `backend/pixelflow/video_agent/thinking_stream.py`

## 核心逻辑

1. FE：确认卡以外 NL → fallthrough turns/start（已删除 `resolveWorkflowResumeIntent`）
2. `needs_user_reply` 时 seed script，不落 Plan
3. 思考流出 `steps` 时跳过 Planner；无 steps 才兜底规划，并在推进前确保成稿种子

## 注意事项

- 「继续 / 开始生图」由服务端思考流选 Tool，前端不得再 push 无断点文案
- 思考流规则：继续资产/开始生图 → continue_* intent + 对应工具；未确认脚本不要直接 prepare_scene_packages
- Plan 卡 `public_goal` 必须等于思考流 answer，避免气泡与执行方案分叉
