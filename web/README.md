# PixelFlow Web

PixelFlow 前端 —— **对话 + canvas** 工作区(Vite + React + TS + Tailwind v4)。
左侧对话(chat),右侧 canvas 渲染 Brief / 生成进度 / 成片。

## 开发

```bash
cd web
pnpm install
pnpm dev          # http://localhost:5273
pnpm lint         # tsc 类型检查
pnpm build        # 产物到 dist/
```

`/api` 在 dev 下代理到后端(默认 `http://localhost:8000`,可用 `VITE_API_TARGET` 覆盖):

```bash
VITE_API_TARGET=http://localhost:8123 pnpm dev
```

## 结构

```
src/
  components/layout/    Sidebar(对话列表) + AppLayout
  components/chat/      ChatPanel + MessageBubble
  components/composer/  Composer(输入器) + Chip(参数胶囊)
  components/canvas/    CanvasPanel + BriefCard + VideoResultGrid
  pages/WorkspacePage   对话 + canvas 双栏
  lib/                  types / chat 类型 / utils
```

> 当前 WorkspacePage 用前端 mock 流程驱动交互预览;后续接 `/api/tasks`
> (创建 / SSE 事件 / brief 确认·修订 / 结果·资产)。
