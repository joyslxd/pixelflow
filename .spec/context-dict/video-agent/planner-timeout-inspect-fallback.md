---
topic: V2.1 Planner 超时误降级为 inspect
module: video-agent
date: 2026-08-10
keywords:
  - planning_timeout_sec
  - 规划超时
  - json_schema
  - import_script
  - inspect fallback
---
## 结论摘要
本地输入脚本后出现「执行方案 · 规划超时，先读取项目资料」，不是没生成 plan，而是 `asyncio.wait_for(plan_turn, 10s)` 超时后降级为单步 `inspect_video_workspace`。根因：DeepSeek 结构化输出偶发返回错误形态触发最多 3 次修复，单次约 5–12s，合计易超 10s。

## 相关文件
- `backend/pixelflow/video_agent/entrypoint.py`（超时默认已调至 45s）
- `backend/pixelflow/video_agent/planner/model.py`（json_schema + 输出样例/schema）
- `.spec/context-dict/video-agent/v2.1-planner-main-path.md`

## 核心逻辑
1. Gateway 装配真实 `VideoAgentPlanner`
2. `submit_turn` → `plan_turn`；超时/失败 → `_inspect_fallback_plan`
3. 前端展示的是降级 plan 的 `public_goal`，不是“无 plan”

## 注意事项
- 修复后需热重载/重启 gateway；新对话再试贴脚本
- 若仍超时，先看日志 `VideoAgent planner timed out after …s`
- 不要把 inspect 降级误判为 Planner 未装配
