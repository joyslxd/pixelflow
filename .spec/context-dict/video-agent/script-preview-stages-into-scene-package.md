---
topic: 脚本预览分阶段产物直接投影进视频场景包
module: video-agent
date: 2026-08-13
keywords:
  - script_pipeline
  - characters
  - outline
  - prepare_scene_packages
  - buildAssetPackagePlanMarkdown
  - 视频场景包
  - settings_source_markdown
---
## 结论摘要
右侧「脚本预览 · 分阶段产物」里的 characters（角色/场景/道具设定）与 outline（分镜提示词）已是结构化产物。确认脚本后 `prepare_scene_packages` 应：**镜头读 episode、资产读 characters**，确定性投影到「视频场景包」，禁止再用 `_scene_package_prompt` 二次拆结构。

## 相关文件
- `backend/pixelflow/video_agent/tools/scene_packages.py`（`shot_source_markdown` / `settings_source_markdown`）
- `backend/pixelflow/generate/scene_packages.py`（`fast_path_pipeline`）
- `web/src/features/video-agent/scriptSkillStages.ts`（`buildAssetPackagePlanMarkdown`）
- `backend/pixelflow/video_agent/prompts.py` / `native_invoke.py` / `thinking_stream.py`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`（场景包卡片文案）

## 核心逻辑
1. 资产包正文仍可合并 characters+outline+episode 供展示/兼容
2. prepare 执行：`settings_source=characters`，`shot_source=episode`
3. 有分阶段产物 → 跳过结构 LLM；回复引导打开「视频场景包」卡片

## 注意事项
- outline 与终稿都含分镜标题时，用 snippet 去重，避免整段重复两次
- 导入路径常把设定写在 characters、分镜写在 outline，episode 只是用户稿；缺 characters 时资产会走默认/对白兜底
- 详见 `scene-package-from-pipeline-stages.md` / `script-shots-prefer-episode.md`
