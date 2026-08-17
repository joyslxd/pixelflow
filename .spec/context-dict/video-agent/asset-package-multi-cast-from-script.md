---
topic: 资产包从脚本设定集抽取多角色多道具
module: video-agent
date: 2026-08-08
keywords:
  - extract_script_setting_assets
  - global_assets
  - prepare-scene-packages
  - buildAssetPackagePlanMarkdown
  - characters
  - props
---
## 结论摘要
Video Agent 确认脚本后生成资产包时，旧逻辑依赖 LLM/默认主讲人，且前端可能只传终稿正文、丢掉 `/characters` 阶段，导致 4 人戏塌成单角色、多道具丢失。现改为：前端合并设定集进 `plan_markdown`；后端确定性解析「角色/场景/道具设定」并强制补进 `global_assets`，LLM 结果也按设定清单补齐。

## 关键文件
- `backend/pixelflow/creative/asset_manifest.py`（`extract_script_setting_assets`）
- `backend/pixelflow/generate/scene_packages.py`（`_ensure_script_setting_assets`）
- `web/src/features/video-agent/scriptSkillStages.ts`（`buildAssetPackagePlanMarkdown`）
- `web/src/features/legacy-workspace/LegacyWorkspace.tsx`

## 核心逻辑
1. FE/BE：`buildAssetPackagePlanMarkdown` / `_asset_package_plan_markdown` = characters + outline（缺分镜段时）+ export/episode/终稿
2. BE：从 Markdown 设定章节解析全部角色/场景/道具种子
3. 规则兜底与 LLM 归一化后都调用 `_ensure_script_setting_assets` 强制补齐
4. Prompt 附带「必提取角色/场景/道具」清单；plan.md 截断放宽到 12000 字
5. 指令口径：脚本预览分阶段产物已拆好时，直接投影进「视频场景包」，不要让用户重述设定

## 注意事项
- rf-string 里量化必须写成 `{{1,3}}`，否则 `{1,3}` 会被插成元组导致正则失效
- `product_info` 勿再塞整段脚本，避免污染产品名与非人物过滤
- 已生成的旧资产包不会自动变多，需重新「确认脚本并生成资产包」
