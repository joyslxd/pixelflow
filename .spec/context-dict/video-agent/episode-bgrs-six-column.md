---
topic: 首次 episode 注入 bgrs Skill 且强制六列
module: video-agent
date: 2026-08-14
keywords:
  - run_script_skill_stage
  - episode
  - bgrs
  - BGEC-SD2-book-prompts-skill
  - sedance-video-prompts-skill
  - 六列表
---

## 结论摘要

首次 `run_script_skill_stage(episode)` 过去只吃短 `STAGE_PROMPTS`，**不读** bgrs Skill，质量与 Skill 合同脱节。

现改为 Tool 执行 Prompt 内加载 `sedance-video-prompts-skill` 摘录（铁律 1/4/5/11/14、脚本写作指南、质量保证 + cinematic 景别/运镜/速查），并叠加 PixelFlow **六列强制合同**（禁止 △ 文学剧本）。Skill 全文仍不进 Agent 系统提示。

## 相关文件

- `backend/pixelflow/video_agent/skills/bgrs_episode_guidance.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/tests/test_video_agent_prompt_routing.py`

## 核心逻辑

1. `load_bgrs_episode_guidance()`：按章节抽取，lru_cache
2. `_stage_system_prompt("episode")`：STAGE 任务 + 六列合同 + bgrs 摘录
3. 后续 `polish_seedance_shot_prompts` 仍可再按 `seedance-prompt` 润色成片提示词

## 注意事项

- 成稿 `import_script` → `build_import_structure_system_prompt` 的「剧本正文」段已同样注入 bgrs + 六列（与本条对齐）
- 摘录过短/缺文件应 fail-closed，避免空指导静默降级
- 六列合同优先于 Skill 原文 △ 格式
