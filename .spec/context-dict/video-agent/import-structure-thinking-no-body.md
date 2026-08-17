---
topic: 导入拆解 Thought 不得输出正文
module: video-agent
date: 2026-08-15
keywords:
  - import_script
  - import_structure_extract
  - emit_thinking_delta
  - Thought
  - script_pipeline
  - make_generation_progress_on_token
---

## 结论摘要

`import_script` 成稿拆解时，若把 LLM `on_token` 接到 `emit_thinking_delta`，Thought
会把整份「角色/场景/道具 + 六列镜头表」灌进思考流，既冗长又与脚本预览重复。

正确分工：

- **Thought**：短阶段进度（开场 `emit_progress` + 标题里程碑 + 字数心跳）
- **script_pipeline / 脚本预览**：承接拆解 Markdown 正文

同理：`run_script_skill_stage`、`polish_seedance_shot_prompts` 也不再把生成正文灌进 Thought。

## 关键文件

- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/tools/seedance_polish.py`
- `backend/tests/test_generation_progress_on_token.py`

## 核心逻辑

1. `make_generation_progress_on_token`：扫 buffer 命中 `## 角色` 等里程碑 → `emit_progress`
2. 达到字数阈值发「仍在进行…」心跳，正文本身不进入 reasoning
3. `extract_imported_script_structure` 仍内部攒 chunk 写 pipeline，只是侧路回调不再是 thinking

## 注意事项

- 与 `restructure-thinking-stream-stuck.md` 不冲突：防假死靠 progress/心跳，不靠吐全文
- 真模型 thinking（astream reasoning channel）仍走 `report_thinking`，勿误伤
- Gateway 仍须注入 `report_progress`，否则进度也会静默
