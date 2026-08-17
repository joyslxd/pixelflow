---
topic: 场景包全局资产应按脚本预览设定分流
module: video-agent
date: 2026-08-13
keywords:
  - extract_script_setting_assets
  - 开场钩子
  - 补充证明
  - 办公室梳妆台
  - global_assets
  - _stage_templates
  - 角色/场景/道具设定
---

## 结论摘要

用户看到「出场角色」混入地点/道具、「场景」全是开场钩子/补充证明、道具出现 `@{}`：根因是合并设定段扁平混写被整桶抽成角色，场景桶空后 `_default_global_assets` 用 `_stage_templates` 叙事标题顶替物理场景。

修复后：设定抽取优先独立/嵌套 H2，扁平混写按语义重分流；叙事段名黑名单；默认场景资产来自脚本设定或「主拍摄场景」，不再用开场钩子等。

## 相关文件

- `backend/pixelflow/creative/asset_manifest.py`
- `backend/pixelflow/generate/scene_packages.py`
- `backend/tests/test_plan_asset_manifest.py`
- `backend/tests/test_video_scene_packages.py`

## 核心逻辑

1. `_resolve_setting_sections`：独立 H2 → 合并段内嵌套 → 扁平合并段
2. `_rebucket_mixed_setting_assets` + `_classify_setting_entry_kind`：地点/道具移出角色
3. `_is_narrative_beat_name`：过滤开场钩子/补充证明 N
4. `_default_global_assets.scenes`：只用脚本设定场景；缺省「主拍摄场景」
5. 空蓝图需求时仍应用合同 `visual_style`，不丢角色/场景/道具

## 注意事项

- 已生成的脏场景包不会自动纠正；需重新确认脚本并生成资产包
- `_stage_templates` 仍只驱动分镜 title/storyline，禁止再写入 global_assets.scenes
- 导入路径常见外层「## 角色/场景/道具设定」+ 内层三节；两层都要能解析
