# PixelFlow Web

PixelFlow 前端是基于 Vite、React 和 TypeScript 的 Agent 工作台。浏览器只通过 Gateway 的 `/agent` 公开合同读取 Conversation、Snapshot、SSE、Interrupt 和 Workspace 投影。

## 开发

```bash
cd web
pnpm install
pnpm dev          # http://localhost:5273
pnpm prod         # 用 production 环境变量启动 Vite dev server
pnpm lint         # tsc 类型检查
pnpm test:auth-storage # Authorization 本地存储工具测试
pnpm build-dev    # 使用 .env.development，产物到 dist/
pnpm build-prod            # 使用 .env.production，产物到 dist/
pnpm build-borgrise-test   # 使用 .env.borgrise-test，产物到 dist/
pnpm build-borgrise-prod   # 使用 .env.borgrise-prod，产物到 dist/
pnpm build-ec-prod         # 使用 .env.ec-prod，产物到 dist/
```

## 环境变量

Vite 配置会从当前 `web/` 目录读取环境文件：

| 文件 | 当前值 | 使用场景 |
| --- | --- | --- |
| `.env.borgrise-test` | `https://test-video.borgrise.com` | `pnpm build-borgrise-test` |
| `.env.borgrise-prod`、`.env.production` | `https://video.borgrise.com` | `pnpm build-borgrise-prod`、`pnpm build-prod` |
| `.env.ec-prod` | `http://creator.vitamazing.top` | `pnpm build-ec-prod` |
| `.env.development` | `http://creator.vitamazing.top` | `pnpm dev`、`pnpm build-dev` |

支持的变量：

- `VITE_API_TARGET`：开发服务器把 `/agent` 代理到的目标。
- `VITE_CONTENT_APP_TARGET`：开发服务器把所有 `/api/...` 请求代理到的 content-app 目标。
- `VITE_CONTENT_APP_ORIGIN`：浏览器与 Content-App 同域时使用的公开根地址；不同域时上传改走同域 `/api` 代理。

当前 development 默认走测试 content-app。如果要联调本机 PixelFlow 后端：

```bash
VITE_API_TARGET=http://localhost:8001 pnpm dev
```

如果后端临时端口是 8123：

```bash
VITE_API_TARGET=http://localhost:8123 pnpm dev
```

## 结构

```
src/
  components/layout/    Sidebar(对话列表) + AppLayout
  features/agent-runtime/    Snapshot/SSE reducer 与 Conversation 生命周期
  features/agent-workspace/  AgentWorkspace、InterruptHost 与三栏 Shell
  features/video/            视频 Workspace 投影
  api/                       Gateway 公开合同 Client
  pages/AuthTokenPage   本地调试 content-app Authorization 的设置页
  lib/                  authStorage 与通用显示工具
```

根路由和 `/c/:conversationId` 均直接渲染 `AgentWorkspace`；不保留 Legacy Workspace、浏览器任务轮询或 `/agent/flows` 根任务 API。

## 本地 Authorization 调试

PixelFlow 前端所有后端请求都通过 `src/lib/api.ts` 统一调用。这个 API Client 会从 `src/lib/authStorage.ts` 读取 Authorization，并自动加到请求头里。

本地不经过 content-app 前端时，打开：

```text
http://localhost:5273/agentfrontend/#/auth-token
```

粘贴 content-app 登录 token 后点击“保存并验证”。页面会把 token 保存到 `localStorage.Authorization`，并调用 `/agent/auth/me` 验证当前用户。验证通过后，回到工作台即可调用 Gateway 的 Conversation、Harness Run、SSE 与 Workspace 接口。
