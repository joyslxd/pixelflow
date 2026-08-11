---
topic: 导入缺项与思考流时长应对齐
module: video-agent
date: 2026-08-11
keywords:
  - import_script
  - missing_requirements
  - 视频时长
  - timecode
---

## 结论摘要

思考流认出「总时长 180s」后，导入结论不得再报「仍缺少：视频时长」。`import_script` 缺项复用 `missing_creative_production_fields`（只追问画幅/CTA）；时长用显式「时长 N」或分镜时间码末镜结束秒写入 `duration_sec` 并写进 public_summary。

## 关键文件

- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/production_fields.py`
- `web/src/features/video-agent/scriptSkillStages.ts`

## 核心逻辑

1. `_missing_requirements` → `missing_creative_production_fields`
2. `resolve_script_duration_sec`：显式时长 > 末镜结束秒 > 短 brief 的 N秒
3. 前端 notice 回显真实缺项，不写死三项

## 注意事项

禁止把首镜 `0—10秒` 当成总时长 10；禁止 import 与 creative confirm 各维护一套缺项正则。
