---
topic: 系统提示路由与 STAGE 执行 Prompt 分离
module: video-agent
date: 2026-08-13
keywords:
  - VIDEO_AGENT_SYSTEM_PROMPT
  - STAGE_PROMPTS
  - import_script
  - run_script_skill_stage
  - Tool 路由
  - 八阶段
---

## 结论摘要

原生 Agent 系统提示只负责「何时调哪个已注册 Tool」与确认/话术闸门；脚本写作与成稿拆解质量由 Tool 执行 Prompt 承担（`STAGE_PROMPTS`、`build_import_structure_system_prompt`、`_stage_system_prompt`）。禁止在系统提示里重演 `/start→…→/export` 固定八阶段 Workflow。

## 相关文件

- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/pixelflow/video_agent/tools/script.py`（`ImportScriptTool` description）
- `backend/pixelflow/video_agent/tools/scene_packages.py`（prepare / generate_scene_assets description）
- `backend/tests/test_video_agent_prompt_routing.py`

## 核心逻辑

1. Agent 读 Workspace → 按意图选 1–3 个 Tool（import / skill stage / prepare / assets / scenes…）
2. `run_script_skill_stage`：按缺口选 stage；写作细则在 `_stage_system_prompt(stage)` = 角色 + `STAGE_PROMPTS[stage]` + 命名硬约束
3. `import_script`：拆解器用 `build_import_structure_system_prompt()` 复用 characters+outline 精华
4. Tool `description` 只说明何时用、与相邻 Tool 边界；不把长篇写作模板塞进 Registry 文案

## 注意事项

- 改写作质量 → 改 `STAGE_PROMPTS` / import 拆解 Prompt，不要往 `prompts.py` 堆八阶段细则
- 改路由/闸门 → 改 `prompts.py` 与各 Tool description
- 旧 Planner/thinking_stream 仍有 capability 诊断文案，与原生系统提示职责不同，勿混为一谈
- Path A 可按需单阶段调用 skill stage，不是必须串完八阶段
