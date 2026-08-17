---
topic: import_script 多阶段拆解审核优化写入 script_pipeline
module: video-agent
date: 2026-08-13
keywords:
  - import_script
  - extract_imported_script_structure
  - _split_import_structure_markdown
  - build_import_structure_system_prompt
  - script_pipeline
  - 脚本预览
  - 分阶段产物
---

## 结论摘要

成稿 `import_script` 不能只切 characters/outline。`build_import_structure_system_prompt` 要求一次产出六段固定二级标题（设定/分镜大纲/剧本正文/五维自检/合规/导出终稿），`_split_import_structure_markdown` 按阶段边界切分并写入 `script_pipeline`，右侧「脚本预览 · 分阶段产物」才能看到审核与优化结果。

## 相关文件

- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/tools/script.py`（`ImportScriptTool`）
- `backend/tests/test_video_agent_script_tools.py`
- `backend/tests/test_video_agent_prompt_routing.py`

## 核心逻辑

1. LLM 按固定 H2 输出多阶段 Markdown（复用 STAGE_PROMPTS）
2. 切分只把「已识别阶段标题」当边界；中间未映射 H2（如旧稿 ## 场景设定）并入上一阶段
3. `extract_imported_script_structure` 写入 characters/outline/episode/review/compliance/export
4. 若模型未产出 episode，才用用户成稿兜底；用户原文仍在 `script.content`

## 注意事项

- 兼容旧别名：`## 分镜提示词`→outline，`## 脚本评审`→review 等
- 资产包仍优先 characters+outline；export/episode 作终稿
- 改写作质量改 STAGE_PROMPTS / import system prompt；改切分映射改 `_IMPORT_SECTION_STAGE_PATTERNS`
