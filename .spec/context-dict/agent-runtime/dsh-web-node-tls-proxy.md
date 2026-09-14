---
topic: 本机 npx dsh web 访问 api.deepseek.com 失败
module: agent-runtime
date: 2026-09-14
keywords:
  - dsh web
  - 127.0.0.1:3080
  - SELF_SIGNED_CERT_IN_CHAIN
  - NODE_EXTRA_CA_CERTS
  - api.deepseek.com
---
## 结论摘要

`npx @deepseek-ai/dsh web` 跑在 `127.0.0.1:3080`。页面报 `DeepSeek API request to https://api.deepseek.com failed` 时，curl/Python 往往能通，Node 25 内置 `fetch` 会因 `SELF_SIGNED_CERT_IN_CHAIN` 失败。本机实测：leaf 是 TrustAsia DV TLS RSA CA 2025，根是 DigiCert Global Root G2；给 Node 加上 `NODE_EXTRA_CA_CERTS`（certifi/系统 CA）后 `fetch` 即可到达 API。另：Chrome 打开 `http://127.0.0.1:3080` 时 Origin 不含端口，本地 `/api` 会 403，应改用 `http://localhost:3080` 或终端打印的带 token 地址。

## 关键文件

- `~/.dsh/settings.yaml`（模型选择）
- `~/.dsh/.credentials.yaml`（禁止打印 Key）
- 官方讨论：[Origin 403](https://github.com/deepseek-ai/deepseek-harness/discussions/910)

## 核心逻辑

1. dsh 用 Node `fetch` 直连 `https://api.deepseek.com`，TLS 失败会被包装成 TRANSPORT 文案，看不到 OpenSSL 原因。
2. macOS curl 走系统钥匙串，Node 走自带 CA；TrustAsia 链在 Node 25.9.0 上默认不通过。
3. 这与 PixelFlow Sidecar `:8090` 不是同一进程。

## 注意事项

- 不要把 `DEEPSEEK_API_KEY` 写进回复、日志或仓库。
- 重启 `dsh web` 会换浏览器登录 token，必须用终端新打印的 URL。
- 不要用 `NODE_TLS_REJECT_UNAUTHORIZED=0` 作为常规方案。
