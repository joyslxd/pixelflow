---
topic: Entrypoint 收敛为控制面协调器
module: video-agent
date: 2026-08-12
keywords:
  - VideoAgentEntrypoint
  - Planner
  - generate_scene_assets
  - script_plan_confirmed
  - ScenePackageCompletionProjector
  - entry_path
---
## 结论摘要
按 V2.1 控制面审查收敛入口：正常主链路不再硬编码 `generate_scene_assets`，也不再因「同意/继续」关键词直接写 `script_plan_confirmed`。确定性代码只做允许/禁止（例如要参考图但无资产包 → WAITING）。Tool 选择回给 `VideoAgentPlanner`；规划失败降级仍可单步 `import_script` / `generate_scene_assets` / `inspect`。Operation 完成回填抽到 `operations/projector.py`。角色完整度改为 Intake digest 证据，不再改写 `latest_input` 确认位。

## 关键文件
- `backend/pixelflow/video_agent/entrypoint.py`
- `backend/pixelflow/video_agent/operations/projector.py`
- `backend/pixelflow/video_agent/planner/model.py`
- `backend/tests/test_video_agent_entrypoint.py`

## 核心逻辑
1. Intake wants assets + 无 packages → WAITING（forbid）
2. 有 packages / hydrate 成功 → 走 Planner（Stub/真实均须选 Tool）
3. 删除入口 NL 确认短路；确认事实留给 Confirmation API / Tool 成功 patch
4. `entry_path` 仍可写诊断字段与 polish episode 兼容种子，但不展开步骤表

## 注意事项
- polish `script_pipeline.episode` 种子仍是兼容债，下一批应迁到 `import_script`
- field_followup 仍会写 script（生产字段补丁）；后续可迁到专用 Workspace Command
- 前端「同意方案」按钮应继续走结构化确认，不要依赖入口关键词
