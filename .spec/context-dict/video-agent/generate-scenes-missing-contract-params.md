---
topic: generate_scenes 启动失败（Provider 缺创作合同参数）
module: video-agent
date: 2026-08-14
keywords:
  - generate_scenes
  - ProviderJobCallError
  - creation_contract
  - build_scene_provider_request
  - 分镜视频未能启动
---

## 结论摘要

用户点「确认并生成分镜视频」后 bootstrap `generate_scenes` 报「分镜视频未能启动」。日志根因是 `ProviderJobCallError: provider_call_failed`。

真实原因：`M06SceneGenerationOperationPort` 默认 `_default_provider_request` 只拷贝镜头字段，场景包镜头通常只有 `prompt`/`duration_ms`/`shot_description.mentions`，不含 `model`/`ratio`/`size`/`sound`/`generation_mode`。下游 `make_scene_video_job_service` → `_scene_video_request` 校验缺字段抛 `ContentAppTaskContractError`，被 Adapter 吞成无 cause 的 `ProviderJobCallError`。

修复：默认走 `build_scene_provider_request(context, scene, variant)`，从 `creation_contract` 补齐视频参数，从 mentions/`image_urls` 收集 HTTPS 参考图并推断 `generation_mode`；`ProviderJobCallError`/`VideoToolExecutionError` 在 bootstrap 中回传可读中文。

## 相关文件

- `backend/pixelflow/video_agent/adapters/scene_operation.py`
- `backend/pixelflow/video_agent/native_invoke.py`
- `backend/pixelflow/skills/borgrise/provider_jobs.py`（`_scene_video_request`）
- `backend/tests/test_video_agent_scene_operation.py`

## 核心逻辑

1. `creation_contract.video_model/ratio/size/sound` → Provider request
2. `duration_ms // 1000` 或 `duration_sec` → `duration`
3. mentions/`image_urls` → `image_urls`；有参考素材则 `reference_mode_video`
4. 缺合同参数时 fail-closed，不调用供应商

## 注意事项

- Adapter 仍会把非 402/404 异常映射为无详情的 `ProviderJobCallError`；业务侧应在进 Provider 前补齐参数
- 自定义 `provider_request_builder` 仍可注入，跳过合同补齐
- 后端 `--reload` 后生效；无需改前端
