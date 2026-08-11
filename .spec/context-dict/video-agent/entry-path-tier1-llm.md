---
topic: VideoAgent 入口路径方案 1（已废止）
module: video-agent
date: 2026-08-10
keywords:
  - entry_path
  - DeepSeekEntryPathModel
  - DEPRECATED
  - V2.1
---
## 结论摘要
**已废止。** V2.1 批次 A 起，Turn 主路径改为 `VideoAgentPlanner.plan_turn()`（见 `v2.1-planner-main-path.md`）。`entry_path` / `DeepSeekEntryPathModel` 仅可写入 workspace 诊断或成稿种子，不再展开完整步骤表。

## 相关文件
- `.spec/context-dict/video-agent/v2.1-planner-main-path.md`
- `docs/superpowers/specs/2026-08-10-video-agent-v2.1-control-plane-design.md`
