---
topic: episode 六字段 + 画面 @参考图写入镜头描述
module: video-agent
date: 2026-08-15
keywords:
  - 景别
  - 运镜
  - 画面
  - 旁白（对白）
  - 旁白/对白
  - 旁白/對白
  - 旁白（對白）
  - 屏幕文案
  - 行动引导
  - Markdown 表格
  - @yann
  - 形象参考
  - mentions
  - asset_requirements
  - ensure_narration_in_shot_description
---

## 结论摘要

场景包镜头描述此前常有三类缺口：

1. **六字段**：episode 标准「景别/运镜/画面/旁白（对白）/屏幕文案/行动引导」须整段写入 `shot_description`（已有 `_block_shot_fields`）。
2. **@参考图**：画面里的 `人物形象@yann`、`女2形象参考@安然` 未进入 `asset_requirements`，`_blueprint_reference_asset_ids` 得到空列表，`mentions` 为空，正文 `@yann` 不会变成可点的 `@asset_id`。
3. **Markdown 镜头表**：脚本预览里常见 `| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | … |` 表格。旧解析只认「镜头N + 时码」「N—M秒｜标题」「## 镜头N」，表格行带 `|` 时抽到 0 镜 → `prepare` 回退机械模板，六字段与 `@` 全部丢失。

补充（2026-08-15）：

4. **繁体「旁白/對白」**：未进 `_FIELD_ALIASES` / 行内标签正则时，整列被当成 prose；若镜块已有景别/画面等，prose **不会**并入 `shot_description` → 分镜表缺旁白。简繁别名 + `ensure_narration_in_shot_description`（prepare 再兜底）已修。
5. 分镜 UI 已去掉底部独立旁白框，旁白只能出现在镜头描述六字段；缺行即「看不见对白」。

修复：

- 抽取时把 `@token` 写入 `asset_requirements`；组装场景包时优先从镜头正文解析 `@` 并绑定 `global_assets`（展示名大小写不敏感，保留「人物形象@…」上下文位置）。
- `_parse_markdown_shot_tables`：识别含「时间」+ 六字段列的 Markdown 表，合成带标签镜块再走 `_append_shot_entry`；表头「旁白/对白」「旁白/對白」归一为「旁白（对白）」。
- `@安然盯着…` 会对设定集做最长前缀匹配，并允许 `@安然` 后紧跟汉字替换；避免造出假角色名冲掉设定集。

## 相关文件

- `backend/pixelflow/creative/script_shots.py`
- `backend/pixelflow/generate/scene_packages.py`
- `web/src/lib/shotDescriptionDisplay.ts`
- `backend/tests/test_script_shot_extraction.py`
- `.spec/context-dict/video-agent/episode-six-fields-into-shot-description.md`

## 核心逻辑

1. `_parse_shot_entries_from_text`：无传统镜块时回退 `_parse_markdown_shot_tables`
2. `_asset_requirements_from_shot_text`：扫 `@名字`
3. `_align_global_assets_to_blueprints` + `_resolve_requirement_against_global_assets`：requirement 名精确/最长前缀落到设定集，可跨 characters→scenes
4. `_blueprint_reference_asset_ids`：正文 `@` 优先 → requirements 补缺；同名去重
5. `_ensure_reference_asset_tokens`：`@yann` / `@安然` 原地换成 `@asset_id`（中文名后允许接汉字）
6. `ensure_narration_in_shot_description`：有真实 narration 却缺旁白行时补 `旁白（对白）：…`

## 注意事项

- 已生成的旧场景包需再「确认脚本」/重跑 prepare 才会重投影
- `@` 名必须能在角色/场景/道具设定里匹配到（精确或为展示名前缀）；匹配不到仍可能按贪婪 token 造新资产
- 旁白里的「安然：」裸名默认不强制改成 @，以免对话主语被误绑
- 脚本预览把 `|…|` 渲成 HTML 表；存储仍是 Markdown，不要只看预览形态
- FE 解析把「旁白/對白」等别名归一为「旁白（对白）」再展示/回写
