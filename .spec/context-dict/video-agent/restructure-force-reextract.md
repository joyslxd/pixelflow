---
topic: 重新拆解被同指纹 replay 跳过
module: video-agent
date: 2026-08-15
keywords:
  - 重新拆解脚本
  - import_script
  - force_reextract
  - request_fingerprint
  - replay
  - episode
---

## 结论摘要

「重新拆解脚本」failsafe/模型再次调用 `import_script` 时，若 markdown 与上次导入相同，会命中 `request_fingerprint` → `_replay_result`（「已复用脚本版本」），**完全不跑** `extract_imported_script_structure`。于是 UI 看似拆解成功，`script_pipeline.episode` 仍是旧产物。

模型 Thought 里纠结「要不要先 inspect 拿全文」部分合理但不必要：`import_script` 本就可省略 markdown，由服务端读 Workspace；正确调用是 `import_script(force_reextract=true)`。

## 关键文件

- `backend/pixelflow/video_agent/tools/script.py`
- `backend/pixelflow/video_agent/native_invoke.py`（failsafe 传 force_reextract）
- `backend/pixelflow/video_agent/prompts.py`

## 核心逻辑

1. `ImportScriptInput.force_reextract`：true 时跳过 fingerprint replay，清空旧 pipeline 再写入新拆解
2. `markdown` 可空，执行前从 `script.content` / `latest_input` 注入
3. failsafe：`arguments={"markdown": …, "force_reextract": True}`
4. 入模提示：禁止为找全文而 inspect；直接 `force_reextract=true`

## 注意事项

- 普通粘贴重复导入仍走 replay（防重复计费/重复拆解）
- force 时摘要为「已重新拆解脚本版本 N」
- episode 质量另依赖 bgrs 注入的 import structure prompt；本条只保证「会重跑」
