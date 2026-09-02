---
topic: Harness 底座系统指令不得写入领域 Tool
module: agent-runtime
date: 2026-09-02
keywords:
  - system_instruction
  - compose_system_instruction
  - Skill
  - replace_existing
  - 领域差异
---
## 结论摘要

Harness 系统指令只承载跨领域事实来源、Tool Broker 边界、确认与沟通要求。视频选 Tool、Seedance Prompt、asset_registry 等规则属于视频 Skill 与 Tool；已有分镜的整包覆盖由 `replace_existing` 校验强制。确认/表单/授权/跑飞恢复必须复用同一底座，只追加本轮触发约束。

## 关键文件

- `backend/pixelflow/agent_harness/system_instruction.py`
- `backend/app/gateway/routers/pixelflow_conversations.py`
- `backend/pixelflow/agent_harness/recovery.py`
- `backend/skills/skills/pixelflow-video-orchestration/SKILL.md`
- `backend/pixelflow/agent_tools/video/storyboard.py`

## 核心逻辑

1. `compose_system_instruction(trigger_type)` = 底座 + 可选 overlay。
2. 领域扩展改 Skill + Tool，不改 Plugin / 通用 Harness 指令。
3. 选 Tool 建议可随 Skill 演进；不可绕过前置必须在 Tool.execute 拒绝。

## 注意事项

- 不要把 PPT/Excel/搜索 Tool 名写回底座。
- 恢复 overlay 也不得出现领域 DTO 字段。
