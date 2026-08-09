---
topic: 步骤卡片跳转脚本阶段段落
module: video-agent
date: 2026-08-08
keywords:
  - AgentPlanTimeline
  - AgentScriptPreviewPanel
  - script_pipeline
  - change_summary
  - 查看本步新增
---
## 结论摘要
八阶段 Skill 产物存在 `workspace.script_pipeline`；右侧预览按阶段分节展示。时间线不再统一写「在右侧查看结果」，改为「查看本步新增：{阶段}」并展示 change chips；点击后滚动到对应章节高亮。

## 关键文件
- `web/src/features/video-agent/AgentPlanTimeline.tsx`
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/features/video-agent/scriptSkillStages.ts`
- `web/src/features/video-agent/state/workspace.ts`（`scriptStages`）
- `backend/.../script_skill_pipeline.py`（`change_summary` + 稳定 H2）

## 核心逻辑
1. Snapshot 投影 `payload.script_pipeline` → `scriptStages`
2. 步骤标题 `/review` 或 artifact `video-script-review-*` 映射阶段
3. 预览 `focusStageId` → `scrollIntoView` + 高亮边框
4. 无终稿 `script` 时，仅有 stages 也可打开右侧预览（保存需等 episode/export）

## 注意事项
- 旧会话若无 pipeline，仍回退到整篇 `script.content` 预览
- 保存仍只写权威 `script`；阶段分节是只读浏览
