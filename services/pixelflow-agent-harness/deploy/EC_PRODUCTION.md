# EC 生产部署

目标为在 EC 生产服务器同机运行 Gateway、Harness Sidecar 和 Agent 前端；浏览器只访问 `http://creator.vitamazing.top/agentfrontend/` 与 `http://creator.vitamazing.top/agent/`。

## 环境 Profile

| 部署目标 | `PIXELFLOW_CONFIG_ENV` | Content-App 根地址 | Agent 前端入口 |
| --- | --- | --- | --- |
| 博观测试 | `borgrise-test` | `https://test-video.borgrise.com` | `https://test-video.borgrise.com/agentfrontend/` |
| 博观生产 | `borgrise-prod` | `https://video.borgrise.com` | `https://video.borgrise.com/agentfrontend/` |
| EC 生产 | `ec-prod` | `http://creator.vitamazing.top` | `http://creator.vitamazing.top/agentfrontend/` |

`BORGRISE_BASE_URL` 是 Gateway 调用 Content-App 鉴权、图片和视频 API 的根地址；它不是浏览器访问 Agent 的地址。Profile Loader 会从对应的 `backend/config.<profile>.yml` 写入该值，并由调用方补齐 `/api`。

## EC 服务器受保护配置

在 `services/pixelflow-agent-harness/deploy/.env.harness-release` 中设置非敏感发布身份：

```dotenv
PIXELFLOW_CONFIG_ENV=ec-prod
PIXELFLOW_GATEWAY_IMAGE=pixelflow-gateway:ec-<commit>
PIXELFLOW_HARNESS_IMAGE=pixelflow-harness:ec-<commit>
PIXELFLOW_GATEWAY_DATA_DIR=/var/lib/pixelflow-ec/gateway
PIXELFLOW_HARNESS_DATA_DIR=/var/lib/pixelflow-ec/harness
PIXELFLOW_SKILL_ROOT_HOST=/var/lib/pixelflow-ec/agent-home
```

`.env.gateway` 与 `.env.sidecar` 只保存各自的 Secret、JWT 合同和 Provider 开关；不要在其中设置 `BORGRISE_BASE_URL` 或 `PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES`，避免覆盖 Profile。部署脚本会由 `ec-prod` 自动生成共享 Run 限额与 Manifest 摘要。

## 构建 Gateway 与 Sidecar

在已解包的 Release 根目录运行：

```bash
bash services/pixelflow-agent-harness/deploy/build-and-start-linux.sh
```

该命令仅重建 `pixelflow-gateway` 与 `pixelflow-harness-sidecar`，随后检查四个 loopback 健康端点；不会调用模型或媒体 Provider。

## 构建与发布前端

```bash
cd web
pnpm install --frozen-lockfile
pnpm build-ec-prod
install -d -m 0755 /var/www/pixelflow-agentfrontend
rsync -a --delete dist/ /var/www/pixelflow-agentfrontend/
```

前端 Gateway 请求始终使用相对路径 `/agent/...`，因此 EC 同域 Nginx 代理后不需要浏览器 CORS 配置。

## Nginx 接线

将以下片段合入 `creator.vitamazing.top` 的现有 `server` 块；不要覆盖 Content-App 的其它 location：

```nginx
location = /agentfrontend {
    return 308 /agentfrontend/;
}

location ^~ /agentfrontend/ {
    alias /var/www/pixelflow-agentfrontend/;
    try_files $uri $uri/ /agentfrontend/index.html;
}

location ^~ /agent/ {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
}
```

执行 `nginx -t` 成功后再 reload。Gateway `8001` 与 Sidecar `8090` 必须保持仅 loopback 绑定，不能在安全组或 Nginx 中直接公开 Sidecar。

## 不计费验收

```bash
curl -fsS http://127.0.0.1:8001/live
curl -fsS http://127.0.0.1:8001/ready
curl -fsS http://127.0.0.1:8090/live
curl -fsS http://127.0.0.1:8090/ready
curl -fsSI http://127.0.0.1/agentfrontend/
curl -fsSI http://127.0.0.1/agent/ready
```

前五项不调用模型或 Provider。完成后由用户在浏览器打开 Agent 授权页并自行发起首个真实业务请求。
