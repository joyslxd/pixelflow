---
topic: 抽镜头优先读确认后的 episode
module: video-agent
date: 2026-08-13
keywords:
  - resolve_shot_source_markdown
  - script_pipeline.episode
  - extract_script_shot_entries
  - shot_source_markdown
  - prepare_scene_packages
---

## 结论摘要

脚本确认后拆镜数/总时长应读 `script_pipeline.episode`（其次 export / script.content），不要用 characters+outline 拼接的资产包正文当主来源；设定段里的噪声「镜头1」会抢解析，导致镜数塌成 1～2 镜。

## 相关文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/generate/scene_packages.py`（`shot_source_markdown`）
- `backend/pixelflow/video_agent/adapters/domain_jobs.py` / `scene_package_operation.py`
- `backend/pixelflow/video_agent/native_invoke.py` / `agent_runtime/service.py`

## 核心逻辑

1. `resolve_shot_source_markdown(payload)`：episode → export → script → plan_markdown → fallbacks；取第一个能解析 ≥2 镜的正文
2. prepare：`plan_markdown` 仍合并设定供资产抽取；`shot_source_markdown` 单独定镜数
3. 时长推断与「已有包是否需重拆」同样走 episode 优先

## 注意事项

- 旧对话若 episode 空但 script.content 有成稿，仍会回落到 script
- 已生成的错误镜数包需再确认脚本才会按 episode 重拆
