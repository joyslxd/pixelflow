---
topic: 成片已生成后收尾报「本轮处理中断」
module: video-agent
date: 2026-08-17
keywords:
  - 本轮处理中断，请稍后重试
  - APIConnectionError
  - compose_or_export_video
  - MP4成片已生成
  - _public_model_failure_message
  - confirm-video_confirmation
---
## 结论摘要

用户「好了吗」→ 确认后 `compose_or_export_video` **成功**（工具卡「MP4成片已生成」，约 70s）。
随后 Agent 再请求上游 `/chat/completions` 写收尾话术时出现
`openai.APIConnectionError: Connection error.`（日志 16:54:06，turn=`confirm-video_confirmation_91d47`）。
`_public_model_failure_message` 旧逻辑把非 500/429 一律收成「本轮处理中断」，盖住了已成功的成片事实。

## 关键文件

- `backend/pixelflow/video_agent/native_invoke.py`（`_invoke_streaming` except / `_public_model_failure_message`）
- 本地网关日志：`原生 Agent astream 失败` + `APIConnectionError`

## 核心逻辑

1. ReAct：Tool 成功 ≠ 整轮 astream 成功；还要再 round-trip 模型生成最终 AIMessage。
2. 模型连接失败时，应 salvage ToolMessage 的 `public_summary`，不要只抛笼统中断文案。
3. 成片是否在 Workspace：看 `outputs`/`deliveries` 与工具卡；「处理中断」不等于合并失败。

## 注意事项

- 堆栈里若见 `http_proxy` / `start_tls`，多半是本机代理到模型网关不稳，与 content-app merge 无关。
- 用户侧：右侧若已有成片/输出项，无需再点合并计费；刷新即可。
