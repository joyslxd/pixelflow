---
topic: 确认脚本勿把拆解 episode 盖回导入原文
module: video-agent
date: 2026-08-14
keywords:
  - confirm-script-plan
  - sync_shot_source_into_pipeline
  - prefer_structured_shot_markdown
  - script.content
  - episode
---

## 结论摘要

成稿导入后：`script.content` 仍是用户原文，`script_pipeline.episode` 才是拆解规范化正文。脚本预览有 stages 时展示拆解稿，但确认按钮原先把 `draft=script.content` 原文送进确认 API；后端 `sync_shot_source_into_pipeline` 又把原文写回 episode，预览里「剧本正文」瞬间跳回粘贴稿。

修复：

1. FE 未编辑确认传空串，并优先 `stages.export/episode`
2. BE `prefer_structured_shot_markdown`：确认正文若比现有抽镜源更「原文化」，改用现有结构化正文再保存
3. `sync_shot_source_into_pipeline`：结构化分数更高的 episode 禁止被劣质原文覆盖

## 相关文件

- `web/src/features/video-agent/AgentScriptPreviewPanel.tsx`
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`
- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/agent_runtime/service.py`
- `backend/tests/test_script_shot_extraction.py`

## 注意事项

- 用户若在预览里**真正改稿**再确认，仍会用新稿（分数不低于现有时）
- 与 `confirm-script-reprepare-on-edit.md` 配套：只有实质改稿才重拆
