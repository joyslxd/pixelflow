---
topic: polish_seedance_shot_prompts 独立润色 Tool
module: video-agent
date: 2026-08-14
keywords:
  - polish_seedance_shot_prompts
  - seedance-prompt
  - STAGE_PROMPTS episode
  - pre_polish_content
---

## 结论摘要

方案 2：新增已注册 Tool `polish_seedance_shot_prompts`。Agent 只负责在 episode 就绪、`prepare_scene_packages` 之前调度该 Tool；`seedance-prompt` Skill 正文由 Tool 内 `load_seedance_guidance()` 加载，不进系统提示，也不假装调度 Skill 文件。润色结果写回 `script_pipeline.episode.content`，原文进 `pre_polish_content`，幂等指纹命中则复用。

## 相关文件

- `backend/pixelflow/video_agent/tools/seedance_polish.py`
- `backend/pixelflow/video_agent/runtime.py`
- `backend/pixelflow/video_agent/prompts.py`
- `backend/pixelflow/generate/seedance_prompt.py`
- `backend/tests/test_video_agent_seedance_polish.py`

## 注意事项

- 与 `run_script_skill_stage(episode)` 分工：episode 负责文学/结构正文；polish 负责视频模型可执行镜头描述
- 重复润色始终以 `pre_polish_content` 为源，避免叠润色
- `bgec-sd2-book-prompts-skill` 未挂载；PixelFlow 成片合同对齐 `seedance-prompt`
- 未做 native_invoke 强制 bootstrap；当前靠 Agent 路由选 Tool
