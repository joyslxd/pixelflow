---
topic: 脚本预览 14 镜 vs 资产包只有 2 镜
module: video-agent
date: 2026-08-12
keywords:
  - extract_script_scene_blueprints
  - 镜头列表
  - split_video_duration
  - prepare_scene_packages
  - scene_blueprints
---

## 结论摘要

脚本预览展示的是成稿 Markdown 里的「镜头1…镜头14」正文；V2 资产包原先在无 Plan `scene_blueprints` 时走 `split_video_duration(总秒)`，偏好 15s/镜。总时长若仍是默认 30s → 恰好 2 镜，与正文镜数无关。

修复：`prepare` 在缺少权威蓝图时，从脚本解析 `镜头N-「时间码」`（对齐 `plan_video.md`），生成 `scene_blueprints` 并按镜头时间轴定总时长，再要求结构 LLM/规则路径产出相同镜数。

## 关键文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/generate/scene_packages.py`（`_resolve_scene_schedule`）
- `backend/pixelflow/video_agent/tools/scene_packages.py`（时长也读镜头末尾）
- `backend/tests/test_script_shot_extraction.py`

## 核心逻辑

1. 抽镜头条目 → 总时长 = max(脚本末尾, 表单时长, 镜数×4)
2. 镜数超出总时长上限则合并相邻短镜；不足则放弃抽取回退机械切分
3. `repair_scene_blueprints_schedule` 把每镜压到 4–15 秒并连续铺满

## 注意事项

- 已生成的 2 镜包不会自动变 14 镜，需重新确认脚本生成资产包
- 脚本若无时间码镜头列表，仍会回退机械切分
- Seedance 单镜仍 4–15 秒；超长单镜会被加权压缩，短镜可能合并
