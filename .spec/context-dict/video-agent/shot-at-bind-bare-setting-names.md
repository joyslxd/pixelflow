---
topic: 分镜裸名无法变成 @资产引用
module: video-agent
date: 2026-08-14
keywords:
  - @引用
  - asset_requirements
  - mentions
  - 裸名
  - 安然
  - _blueprint_reference_asset_ids
  - _ensure_reference_asset_tokens
---

## 结论摘要

`_AT_TOKEN_PATTERN` / `_asset_requirements_from_shot_text` 只认正文里的 `@名字`。真实成稿与 timeline 常写裸名「安然盯着手机」，抽取得到空的 `asset_requirements`，`_blueprint_reference_asset_ids` 返回空列表，后续 `_ensure_reference_asset_tokens` 的裸名→`@asset_id` 替换根本不会跑，镜头仍是纯文本、mentions 为空。

有 `@yann` 的单测本来就绿；用户感知「没生效」是因为上游正文没有 `@`。

## 相关文件

- `backend/pixelflow/generate/scene_packages.py`
- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/video_agent/tools/script_skill_pipeline.py`
- `backend/tests/test_script_shot_extraction.py`

## 核心逻辑

1. 显式 `@token` → `_reference_ids_from_shot_text`
2. blueprint `asset_requirements` 补缺
3. **新增** `_reference_ids_from_bare_asset_names`：用 `global_assets` 展示名扫镜头正文（长名优先、遮蔽已有 `@`）
4. `_ensure_reference_asset_tokens`：把命中的裸名原地换成 `@asset_id` 并写 mentions
5. episode 提示词要求画面写 `@实体名`，减少以后再出裸名

## 注意事项

- 旧场景包需重新「确认脚本 / 生成分镜包」才会重投影
- 单镜成稿（`entries < 2`）仍会走机械模板，与 @ 绑定无关
- 旁白里的「安然：」也会被裸名扫到；这是可接受的绑定，不是对话主语误绑进 requirements 的旧问题
- Python `\w` 含汉字：不能用 `(?![\w\-])` 卡中文名尾，否则「安然盯着」「Yann把」永远匹配不到；纯 ASCII 名改用 `(?![A-Za-z0-9_\-])`
