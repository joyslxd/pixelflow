---
topic: 左侧历史对话在窄屏/iframe 消失
module: video-agent
date: 2026-08-13
keywords:
  - Sidebar
  - 最近对话
  - hidden xl:flex
  - lg:flex
  - iframe
  - 历史抽屉
---
## 结论摘要
左侧「最近对话」整列不见，常见不是数据删了，而是 `Sidebar` 用了 `hidden xl:flex`：视口/宿主 iframe 宽度 &lt; 1280px 时整栏 CSS 隐藏，且无替代入口。现改为 `lg:flex`（≥1024 显示固定侧栏），&lt;lg 左上角「历史」打开抽屉列表。

## 相关文件
- `web/src/components/layout/Sidebar.tsx`
- `web/tests/responsiveLayout.test.mjs`

## 核心逻辑
1. 固定侧栏：`hidden lg:flex w-[244px]`
2. 窄屏：`lg:hidden` 浮动「历史」+ 遮罩抽屉复用同一列表加载
3. 列表仍走 `api.listConversations`；失败显示错误文案，成功空列表显示「暂无历史对话」

## 注意事项
- 若仍空且文案是加载失败：查 Authorization / 代理环境（dev vs 本地网关）
- 分镜画布全屏层仍用 `xl:`，与侧栏断点刻意分开
