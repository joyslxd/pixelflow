---
topic: 重新拆解脚本直接 failsafe（跳过入模空等）
module: video-agent
date: 2026-08-15
keywords:
  - 重新拆解脚本
  - import_script
  - force_reextract
  - failsafe
  - 思考中卡住
---

## 结论摘要

「重新拆解脚本」若先入模再等空 Turn failsafe，UI 会长时间停在「正在处理「重新拆解脚本」…」（模型 Thought 不进 reasoning channel）。

现改为：识别意图后 **直接** `_failsafe_import_script_restructure(force_reextract=true)`，跳过 astream；思考流接 progress/token。空 Turn recover 仍保留兜底。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/video_agent/tools/script.py`
- `.spec/context-dict/video-agent/restructure-force-reextract.md`

## 核心逻辑

1. open「正在处理…」→ install reporters → 直执 failsafe
2. failsafe 再 announce「正在用当前脚本重新拆解…」并 `import_script(..., force_reextract=true)`
3. 同指纹不再 replay；拆解阶段推 reasoning delta

## 注意事项

- 拆解 LLM 仍可能 1–3 分钟，但应持续有进度，不是假死在开场句
- 与「重新生成分镜包」话术区分
- 这是确定性 Tool 执行，不是用自然语言假冒 Tool Call
