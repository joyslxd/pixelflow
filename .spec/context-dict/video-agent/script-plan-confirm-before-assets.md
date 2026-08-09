---
topic: 脚本方案确认后门禁与多人角色补全
module: video-agent
date: 2026-08-08
keywords:
  - script_plan_confirmed
  - confirm_for_generation
  - 确认脚本
  - 继续生成视频
  - 角色设定
  - exportReady
  - script_pipeline.characters
---
## 结论摘要
用户必须先确认脚本执行方案，才能启动视频资产包。确认按钮**仅在导出脚本产物完成后**显示。角色完备性检查必须合并 `script_pipeline.characters` 阶段产物，不能只扫终稿正文——否则预览里已有角色/场景/道具仍会被误判为缺失。

## 关键文件
- `web/src/features/video-agent/scriptSkillStages.ts`（`workspaceHasExportReady`、corpus 合并）
- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/video_agent/entrypoint.py`

## 核心逻辑
1. `exportReady`：存在 `export` 阶段产物才展示确认按钮
2. `buildScriptReadinessCorpus` / `_workspace_readiness_corpus`：characters + export + episode + script
3. characters 阶段含角色/场景/道具设定时视为齐全，放行确认
4. 未确认 continue → 提示确认；已确认 → 资产包

## 注意事项
- 仅保存脚本不再自动生成资产包
- 镜头正文里的「男1/女1」不能单独作为「缺角色」依据，要以设定章节为准
