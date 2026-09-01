# Sidecar 配置说明

本服务所有配置仅由进程环境或 Secret Manager 注入。不得把真实 token、模型输入、用户身份或供应商原始响应写入 YAML、测试报告或仓库文件。

`PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES` 必须由 Gateway 所用 profile 配置同步注入 Sidecar。它是包含 `video_interactive_v1`、`confirmation_resume_v1` 与 `run_recovery_v1` 的 JSON 对象；Sidecar 会逐字段和 digest 校验 Gateway 冻结的 Run limits，缺失或不一致时 readiness/Run 均失败关闭。

| 环境变量 | 用途 | 取值与影响 |
| --- | --- | --- |
| `PIXELFLOW_AGENT_HOME` | Sidecar 的隔离 `DSH_HOME` 与管理员共享 Skill 根。 | 必填目录；缺失时 `/ready` 返回 `agent_home_unconfigured`，Sidecar 不接 Run。变更后需重启，运行中 Run 不迁移。 |
| `PIXELFLOW_HARNESS_RUN_STORE` | Sidecar Run/Event SQLite 文件位置。 | 可选；默认位于 `$PIXELFLOW_AGENT_HOME/run-events/runs.sqlite3`。变更后需重启，旧持久化事件不会自动搬迁；回滚时恢复原文件即可。 |
| `PIXELFLOW_GATEWAY_JWT_VERIFY_KEY` | Sidecar 校验 Gateway→Sidecar 服务 JWT 签名的验证密钥。 | 必填 Secret/公钥；缺失时 `/ready` 返回 `gateway_jwt_verify_key_unconfigured`。本地测试可使用短期 HS256 测试密钥，生产应使用 Secret Manager 提供的轮换验证材料。 |
| `PIXELFLOW_GATEWAY_JWT_ISSUER` | Sidecar 接受的 Gateway 服务 JWT 签发方。 | 默认 `pixelflow-gateway`；不匹配即 401。变更后需重启，只影响新请求。 |
| `PIXELFLOW_GATEWAY_JWT_AUDIENCE` | Sidecar 接受的 Gateway 服务 JWT 受众。 | 默认 `pixelflow-harness-sidecar`；不匹配即 401。变更后需重启，只影响新请求。 |
| `PIXELFLOW_TOOL_BROKER_BASE_URL` | Sidecar Capability Plugin 调用 Gateway Tool Broker 的内部地址。 | 必填；生产必须使用 HTTPS，M0 本机测试仅允许 `http://127.0.0.1:<port>`。地址缺失或不安全时 `/ready` 拒绝接收新 Run；变更后需重启，运行中 Run 不迁移。 |
| `PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY` | Sidecar 为每次 Tool Call 签发短期 JWT 的 HS256 密钥。 | 必填 Secret；长度至少 32 字符，必须由 Secret Manager 注入。缺失或过短时 `/ready` 拒绝接收新 Run；变更后需重启，只影响新 Run。 |
| `PIXELFLOW_TOOL_BROKER_JWT_ISSUER` | Sidecar→Tool Broker JWT 的签发方。 | 默认 `pixelflow-harness-sidecar`；必须与 Gateway Broker 校验配置一致，不一致时 Tool 调用返回 401。变更后需重启，只影响新 Run。 |
| `PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE` | Sidecar→Tool Broker JWT 的受众。 | 默认 `pixelflow-tool-broker`；必须与 Gateway Broker 校验配置一致，不一致时 Tool 调用返回 401。变更后需重启，只影响新 Run。 |
| `PIXELFLOW_SIDECAR_INSTANCE_ID` | Sidecar 实例的可审计服务身份。 | 必填非空字符串；写入 Tool JWT 的 `service_instance_id`，缺失时 `/ready` 拒绝接收新 Run。变更后需重启，只影响新 Run。 |
| `PIXELFLOW_HARNESS_MODEL_PROFILE` | Sidecar 接受的唯一 PixelFlow 模型档案逻辑名。 | 默认 `deepseek-v4-pro`；请求档案不一致返回拒绝，防止 Run 临时切换模型。修改后需重启，只影响新 Run。 |
| `PIXELFLOW_HARNESS_MODEL_PROFILE_DIGEST` | Gateway 模型档案的 SHA-256 摘要。 | 必填；与 Run DTO 不一致时拒绝新 Run，防止同名档案内容漂移。修改后需重启，只影响新 Run。 |
| `PIXELFLOW_HARNESS_MODEL_ID` | 发往已配置模型 Provider 的实际模型 ID。 | 默认 `deepseek-v4-pro-ga-260813`；变更后需重启，只影响新 Run，必须同时更新 PixelFlow 模型档案摘要。 |
| `PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS` | 单个 Harness SDK 请求的最大等待时间，单位秒。 | 默认 `90`；正数。超时后 Run 以固定安全错误收口，不回显底层异常。修改后需重启，只影响新 Run。 |
| `DEEPSEEK_API_KEY` | DeepSeek/OpenAI 兼容模型 Provider 的访问 Secret。 | 必填 Secret；通过 Secret Manager 注入，缺失时 `/ready` 返回 `model_credential_unconfigured`。禁止写入 Run DTO、日志和配置文件。 |
| `DEEPSEEK_BASE_URL` | DeepSeek/OpenAI 兼容模型 Provider 的基础地址。 | 必填 HTTPS 地址；Ark 使用 `/api/v3` 基础路径。缺失时 `/ready` 返回 `model_endpoint_unconfigured`，修改后需重启且只影响新 Run。 |

Gateway JWT 必须包含 `exp`、`iat`、`iss`、`aud` 和非空 `service_instance_id`。真实模型测试还要求显式设置 `PIXELFLOW_RUN_REAL_M0=1`；它不是运行配置，只是 pytest 的扣费保护开关，默认关闭以避免本地回归误消耗测试 token。
