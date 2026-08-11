---
topic: 场景包结构模型改 Flash + 生成中可看分镜
module: video-agent
date: 2026-08-10
keywords:
  - deepseek-v4-flash
  - deepseek-v4-flash-202605
  - prepare-scene-packages
  - 查看分镜
  - structure_progress heartbeat
---

## 结论摘要

1. 参考图生成中「查看分镜」被 `actionsDisabled` 全局拦截；已放行 `data-allow-when-disabled`，预览条也可点开。
2. `prepare-scene-packages` 结构阶段改用 `deepseek-v4-flash`（供应商名 `deepseek-v4-flash-202605`），替代易卡 20 分钟的 `deepseek-v4-pro`。
3. 结构 LLM 调用加 30s 心跳，执行规划细节会更新「已等待 x 分 xx 秒」，不再假死在同一句。

## 关键文件

- `web/src/components/chat/MessageBubble.tsx`
- `backend/pixelflow/generate/scene_packages.py`
- `backend/config.dev.yml` / `config.prod.yml`

## 注意事项

- 改模型配置后需重启网关。
- Plan/脚本 Skill 仍用 Pro；仅场景包结构生成用 Flash。
- Flash 质量若明显变差可再回切 Pro 或做 A/B。
