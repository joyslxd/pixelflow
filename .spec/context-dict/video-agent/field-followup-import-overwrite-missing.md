---
topic: 补画幅/CTA 后导入仍报缺字段
module: video-agent
date: 2026-08-12
keywords:
  - 9：16
  - 不需要
  - field_followup
  - import_script
  - missing_requirements
  - 规划超时
  - runtimeNotice
---

## 结论摘要

用户先贴成稿、再回「9：16，不需要」、再确认生成资产包仍被追问画幅/CTA，根因有三：
1. Intake 把短补字段标成 `entry_path=create`，旧 field_followup 要求 `entry_path is None`，补丁未落库；
2. `import_script` 只对成稿正文做生产字段 LLM，正文无画幅/CTA → 写入 `missing_requirements`，覆盖用户已补事实；
3. 首轮 Planner 45s 超时后只 inspect，界面在思考结束到执行方案出现之间缺少「处理中」提示。

## 相关文件

- `backend/pixelflow/video_agent/entrypoint.py`（field_followup 放宽、思考事实早写、规划超时 90s、成熟稿超时可 import）
- `backend/pixelflow/video_agent/tools/script.py`（import 合并 latest_input/工作区字段）
- `backend/pixelflow/video_agent/production_fields.py`（`aspect_ratio`/`ending_cta` 落库）
- `web/src/lib/supervisor/runtimeNotice.ts`（Turn/Run 处理中提示）

## 核心逻辑

1. 短补字段：有脚本/缺项时即使 entry_path=create 也 `analyze` → `apply_production_fields_to_script` → form_values
2. import：字段文本 = markdown +【本轮指令】；再与 workspace 已有 ratio/cta 合并后写 missing
3. runtimeNotice：inputQueue processing/accepted 或 run=running 时显示「正在处理中」

## 注意事项

- 全角 `9：16` 仅短跟进会 `normalize_user_text`；长脚本正文不改
- 「不需要」须由字段 LLM 映射 `ending_cta=none`，不要本地正则猜
- 历史会话若 script 已带脏 missing，需用户再发一次画幅/CTA 或重新确认以触发 reconcile
