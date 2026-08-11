---
topic: V2.1 控制面权威设计（必遵）
module: video-agent
date: 2026-08-11
keywords:
  - control-plane
  - VideoWorkspace
  - Tool优先
  - 最小前瞻计划
  - 工作台编辑面
  - 2026-08-10-video-agent-v2.1-control-plane-design
---

## 结论摘要

VideoAgent 后续一切改动必须以 `docs/superpowers/specs/2026-08-10-video-agent-v2.1-control-plane-design.md` 为唯一权威设计。用户始终同一对话入口；系统读 Workspace + 本轮输入 → Agent 选最小可解释 Tool 批次 → 成本/不可逆前确认 → 事实落服务端 → 工作台只展示/编辑/确认，不编排。

## 相关文件

- `docs/superpowers/specs/2026-08-10-video-agent-v2.1-control-plane-design.md`
- `.cursor/rules/02-video-agent-v21-control-plane.mdc`

## 核心逻辑

1. 一个控制面：`VideoWorkspace + Plan/Step + Confirmation + Operation`
2. Tool 优先于固定路径；每 Turn 1–3 步
3. 服务端拥有执行事实；LegacyWorkspace 是编辑面宿主

## 注意事项

- 与本规范冲突的临时 UX（如前端自决 Job、固定 A/B/C 铺全流程）不得作为新主路径引入
- 展示层改动（折叠思考、静默导入卡）不得改变编排主权归属
