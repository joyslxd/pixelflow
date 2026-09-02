---
topic: 改第一段脚本后看板显示生成任务失败
module: agent-runtime
date: 2026-09-02
keywords:
  - deadline_exceeded
  - prepare_scene_packages
  - patch_scene
  - revise_storyboard
  - replace_existing
  - system_instruction
  - 生成任务失败
---
## 结论摘要

用户只改第 1 镜剧情时，Agent 读了 Skill 并调用 `prepare_scene_packages` 全量重建。`video_interactive_v1` 墙钟 300 秒，Run 在真正写入工作区前被 `deadline_exceeded` 终止。看板把 Run `failed` 显示成「生成任务失败」。

根因有两层：选 Tool 文案曾把局部改镜导向整包写入；且视频 Tool 名曾写进 Harness 底座系统指令，与「领域差异由 Skill、Tool、Workspace 表达」冲突。现已拆开：底座只保留跨领域事实/确认/沟通边界；视频选 Tool 规则在编排 Skill 与 Tool description；已有分镜的隐式整包覆盖由 `replace_existing` 校验拒绝。

## 关键文件

- `backend/pixelflow/agent_harness/system_instruction.py`
- `backend/pixelflow/agent_tools/video/scene.py`（`patch_scene`）
- `backend/pixelflow/agent_tools/video/storyboard.py`（`replace_existing`）
- `backend/skills/skills/pixelflow-video-orchestration/SKILL.md`
- `backend/skills/skills/video-script-authoring/SKILL.md`
- `backend/config.dev.yml`（`deadline_seconds: 300`）
- `web/src/features/agent-workspace/AgentTaskBoard.tsx`

## 核心逻辑

1. 局部改镜应走 `patch_scene` 或 `revise_storyboard`，保留已有 `asset_id`。
2. 工作区已有分镜时，`prepare_scene_packages` 缺省拒绝；仅当用户明确要求整份重建并传 `replace_existing=true` 才覆盖。
3. 所有 trigger（用户 Turn、确认/表单/授权恢复、run_recovery）注入同一底座指令，恢复类只追加本轮约束。
4. 未来 PPT/Excel 等能力只改对应 Skill + Tool，不改 Harness 底座。

## 注意事项

- 看板「生成任务失败」= 当前 Run 终态，不等于资产 `state=failed`。
- 改 Tool description 会变更 Manifest digest；运行中 Run 仍冻结旧 digest。
- 选 Tool 建议可以漂移，不可绕过的前置必须写在 Tool 校验里。
