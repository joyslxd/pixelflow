---
topic: import_script 强制结构化拆解
module: video-agent
date: 2026-08-12
keywords:
  - import_script
  - extract_imported_script_structure
  - characters
  - outline
  - 分镜提示词
  - nodes.py creative_node
---

## 结论摘要

`nodes.py:creative_node` 是旧 LangGraph Brief 生成，**不是** VideoAgent 成稿检查入口。成稿判断不得只靠 Intake 口头摘要；`import_script` 导入后必须调用结构化拆解/审核/优化，写入 `script_pipeline` 多阶段产物（characters/outline/episode/review/compliance/export）。

## 相关文件

- `backend/pixelflow/video_agent/tools/script.py`（`ImportScriptTool`）
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`（`extract_imported_script_structure`）
- `backend/pixelflow/video_agent/thinking_stream.py`（禁止 inspect 口头代替拆解）

## 核心逻辑

1. 导入脚本 + 生产字段分析
2. `extract_imported_script_structure` 一次 LLM 产出六段固定标题 Markdown
3. `_split_import_structure_markdown` 按阶段边界切分写入 pipeline
4. 拆解失败不回滚导入，摘要提示可重试

## 注意事项

- 不要把拆解逻辑塞回 `nodes.py`；V2.1 控制面只认 VideoAgent Tool
- Path A 仍可按需调用 `run_script_skill_stage` 单阶段；**不是**必须跑完八阶段
- 「剧本正文」段必须注入 bgrs 摘录 + 六列合同（与 `run_script_skill_stage(episode)` 对齐）；不得只靠短 STAGE_PROMPTS 口头提 bgrs
- 详见 `import-script-multi-stage-optimize.md`、`episode-bgrs-six-column.md`
