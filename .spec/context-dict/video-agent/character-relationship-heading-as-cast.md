---
topic: 角色关系标题被误抽成唯一出场角色
module: video-agent
date: 2026-08-10
keywords:
  - 角色关系
  - extract_script_setting_assets
  - _iter_setting_entries
  - global_assets.characters
  - 安然
---
## 结论摘要
脚本方案卡里已有多人（如安然 / Yann / 联名方代表），但场景包出场角色只剩「角色关系」：因 `/characters` 常输出 `### 角色关系` 容器标题，旧抽取把标题当成人名并提前返回，不再解析其下列表。修复：容器标题丢弃，继续解析子列表/顿号人名；`global_assets` 泛化过滤同步剔除「角色关系」。

## 关键文件
- `backend/pixelflow/creative/asset_manifest.py`
- `backend/pixelflow/generate/scene_packages.py`（`_GENERIC_ASSET_NAMES.characters`）
- `backend/tests/test_plan_asset_manifest.py`

## 核心逻辑
1. `_CONTAINER_SETTING_HEADINGS` 含「角色关系/角色档案」等
2. 命中容器标题时：`push_list_entries(body)`，否则再试「A、B、C三人同框」散文
3. 有独立 `### 安然` 等子标题时仍按原标题抽取
4. 已生成的旧包不会自动变多，需重新确认脚本生成资产包

## 注意事项
- 「核心产品」仍走泛化标题→正文具体名，不要和角色容器混用同一策略
- rf-string 里量化写成 `{{1,3}}` 的旧坑仍在本模块
- 已生成的旧包不会自动变多，需重新确认脚本生成资产包
