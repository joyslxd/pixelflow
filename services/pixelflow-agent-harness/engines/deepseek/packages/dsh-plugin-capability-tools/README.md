# PixelFlow Capability Tools Plugin

该外部 Cordis Plugin 只把模型选择的稳定 Tool 调用转发给 PixelFlow Tool Broker。它不访问数据库、Borgrise、content-app、用户 Authorization 或工作区文件；这些资源只能在 Gateway 的 Broker/Repository 层按 `run_id` binding 回查。

`package.json` 和 `tsconfig.json` 各字段的用途、构建影响与回滚方式沿用 M0 Probe Plugin 的同名配置说明；本包运行时产物为 `dist/index.js`，仅由安全 Cordis Composition 加载。

环境变量由 Sidecar 每个 Run 注入，均不进入模型上下文：

| 环境变量 | 用途与影响 |
| --- | --- |
| `PIXELFLOW_TOOL_BROKER_BASE_URL` | Broker 的内部 HTTPS 地址；仅本机 M0 测试允许 `http://127.0.0.1`。缺失或不安全地址时 Tool 调用 fail-closed。 |
| `PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY` | Sidecar 签发 Broker JWT 的 HS256 密钥；必须由 Secret Manager 注入，缺失时 Tool 调用失败且不重试。 |
| `PIXELFLOW_TOOL_BROKER_JWT_ISSUER` | JWT 签发方，默认 `pixelflow-harness-sidecar`；与 Gateway 校验不一致时 Broker 返回 401。 |
| `PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE` | JWT 受众，默认 `pixelflow-tool-broker`；与 Gateway 校验不一致时 Broker 返回 401。 |
| `PIXELFLOW_SIDECAR_INSTANCE_ID` | Sidecar 实例身份；写入 JWT 的 `service_instance_id`，缺失时 Tool 调用 fail-closed。 |
| `PIXELFLOW_HARNESS_RUN_ID` | 本次 Sidecar Run 标识；仅用于 Broker 回查 binding，不包含用户身份。 |
| `PIXELFLOW_HARNESS_SESSION_ID` | 固定 Harness Session 标识；Broker 对照 Run binding 校验。 |
| `PIXELFLOW_HARNESS_CONTEXT_DIGEST` | 上游冻结上下文摘要；Broker 对照 Run binding 校验。 |
| `PIXELFLOW_HARNESS_TOOLSET_VERSION` | 冻结 Toolset 版本；Broker 对照 Run binding 校验。 |
| `PIXELFLOW_HARNESS_WORKSPACE_REVISION` | 预期 Workspace revision；Broker 拒绝陈旧版本。 |

任何网络错误、非成功状态或不符合稳定 Observation schema 的响应都只转为固定安全错误，不回显 Broker 响应正文。
