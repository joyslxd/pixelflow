---
topic: 改脚本再确认必须重拆分镜资产包
module: video-agent
date: 2026-08-14
keywords:
  - confirm-script-plan
  - scene_packages_source_digest
  - sync_shot_source_into_pipeline
  - prepare_scene_packages
  - 再次确认
---

## 结论摘要

选模阶段「空点确认」应复用已有场景包；但用户**改了可抽镜正文再确认**必须重跑 `prepare_scene_packages`。

旧逻辑只按「包数 vs 镜数」判断是否刷新，改文案不改镜数时会错误复用。现改为：

1. 确认时把像镜头列表的 markdown 回写 `script_pipeline.episode`（预览保存只改 `script.content`，抽镜却优先读 episode）。
2. prepare 成功写入 `scene_packages_source_digest`（抽镜正文 + characters 设定的 SHA-256）。
3. 再次确认：指纹变化或本次回写了 episode → 强制 prepare；否则可复用。

## 相关文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/agent_runtime/service.py`（`confirm_video_agent_script_plan`）
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/video_agent/operations/projector.py`
- `backend/tests/test_video_agent_confirmation_api.py`

## 核心逻辑

- `compute_scene_packages_source_digest` / `sync_shot_source_into_pipeline`
- 复用条件：已有包 ∧ 非镜数不足 ∧ 未回写 episode ∧ 指纹一致

## 注意事项

- 前端同会话仍只 upsert 一张场景包卡，不会另吐历史卡置灰
- 只改编角色设定阶段、且未同步 characters 时，指纹可能不变；设定变更应走阶段保存/导入链路
- 旧包无 digest 时：未改稿可复用；改稿回写 episode 后仍会重拆
