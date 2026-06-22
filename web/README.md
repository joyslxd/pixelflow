# PixelFlow Web

PixelFlow 前端 —— **对话 + canvas** 工作区(Vite + React + TS + Tailwind v4)。
左侧对话(chat),右侧 canvas 渲染 Brief / 生成进度 / 成片。

## 开发

```bash
cd web
pnpm install
pnpm dev          # http://localhost:5273
pnpm lint         # tsc 类型检查
pnpm test:auth-storage # Authorization 本地存储工具测试
pnpm build        # 产物到 dist/
```

`/agent` 在 dev 下代理到后端(默认 `http://localhost:8001`,可用 `VITE_API_TARGET` 覆盖):

```bash
VITE_API_TARGET=http://localhost:8123 pnpm dev
```

## 结构

```
src/
  components/layout/    Sidebar(对话列表) + AppLayout
  components/chat/      ChatPanel + MessageBubble
  components/composer/  Composer(输入器) + Chip(参数胶囊)
  components/canvas/    CanvasPanel + BriefCard + FlowTimeline + VideoResultGrid
  pages/WorkspacePage   对话 + canvas 双栏
  pages/AuthTokenPage   本地调试 content-app Authorization 的设置页
  lib/                  api / authStorage / types / chat 类型 / utils
```

> WorkspacePage 已接 `/agent/flows`
> (创建 / SSE 事件 / 可解释执行时间线 / brief 确认·修订 / 结果·资产)。

## 本地 Authorization 调试

PixelFlow 前端所有后端请求都通过 `src/lib/api.ts` 统一调用。这个 API Client 会从 `src/lib/authStorage.ts` 读取 Authorization，并自动加到请求头里。

本地不经过 content-app 前端时，打开：

```text
http://localhost:5273/auth-token
```

粘贴 content-app 登录 token 后点击“保存并验证”。页面会把 token 保存到 `localStorage.Authorization`，并调用 `/agent/auth/me` 验证当前用户。验证通过后，回到工作台即可正常调用 `/agent/flows`、SSE 和资产接口。
