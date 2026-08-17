---
topic: 场景包资产来自 characters、镜头来自 episode
module: video-agent
date: 2026-08-13
keywords:
  - settings_source_markdown
  - script_pipeline.characters
  - shot_source_markdown
  - _scene_package_prompt
  - prepare_video_scene_packages_with_llm
  - fast_path_pipeline
---

## 结论摘要

脚本确认后的 prepare **不再**用 `_scene_package_prompt` 二次拆角色/场景/道具：
- 镜头 / 时长：`script_pipeline.episode`（`shot_source_markdown`）
- 角色 / 场景 / 道具：`script_pipeline.characters`（`settings_source_markdown`）
有任一（或 Plan asset_manifest）即走确定性规则路径，`llm_used=False`。

## 相关文件

- `backend/pixelflow/generate/scene_packages.py`
- `backend/pixelflow/video_agent/tools/scene_packages.py`
- `backend/pixelflow/video_agent/adapters/domain_jobs.py` / `scene_package_operation.py`
- `backend/tests/test_script_shot_extraction.py`

## 核心逻辑

1. Tool 分别解析 episode → `shot_source_markdown`、characters → `settings_source_markdown`
2. `_default_global_assets(plan_markdown=settings)` 用 `extract_script_setting_assets`
3. `prepare_*_with_llm`：有蓝图 / settings / manifest → `fast_path_pipeline`，不调结构模型
4. 无 pipeline 的旧 Plan 路径仍可走 LLM + 规则兜底

## 注意事项

- characters 为空时仍可用 episode 拆镜，资产回落到默认/对白兜底
- 已生成的脏包需再确认脚本才会按新路径重投影
- `_scene_package_prompt` / `_ScenePackageNdjsonStream` / `with_llm` 结构分支对确认脚本主链路已是遗留死代码

## 延期 TODO（先记不改）

见实施方案 `docs/superpowers/plans/2026-08-12-native-video-agent.md` **§6 TODO-6.1**：

- 删除 `_scene_package_prompt`、NDJSON 流解析、结构 LLM 调用链
- `PrepareScenePackagesJobService` 只保留规则路径
- 清理 `test_video_scene_packages.py` 中一批 `with_llm` 用例
- 明确无 pipeline 旧 Plan 的降级策略后再硬删
