# Linux Sidecar 部署

Sidecar 可独立部署在 Linux 云服务器；本地 Gateway 通过私网 HTTPS 调用它。不要把它暴露到公网，也不要把任何 Secret 写进仓库。

1. 在服务器准备活动 Skill 根，例如 `pixelflow/backend/skills`，并设置为 `PIXELFLOW_AGENT_HOME`。
2. 在 `deploy/.env.sidecar` 通过 Secret Manager 写入配置说明中列出的 JWT、模型和 Tool Broker Secret。
3. 构建镜像：`docker build -t pixelflow-harness:版本号 services/pixelflow-agent-harness`。
4. 使用 `docker compose -f deploy/docker-compose.linux.yml up -d` 启动。
5. Gateway 配置 `PIXELFLOW_HARNESS_SIDECAR_BASE_URL` 为 Sidecar 的私网 HTTPS 地址，并使用同一组 Gateway→Sidecar JWT 配置。

真实验收通过 GitHub Actions 的手工 `run_real_sidecar=true` 触发。环境 `harness-sidecar-real-test` 必须保存隔离模型 Secret；缺失 Secret 会失败关闭，而不是跳过测试。
