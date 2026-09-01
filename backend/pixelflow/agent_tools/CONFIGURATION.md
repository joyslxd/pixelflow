# Gateway Tool Broker 配置与 Manifest 说明

## 服务身份

| 环境变量 | 用途 | 取值与影响 |
| --- | --- | --- |
| `PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY` | Gateway 校验 Sidecar→Tool Broker 服务 JWT 签名的验证密钥。 | 必填 Secret/公钥；缺失或签名无效时返回 `401 agent_tool_service_authentication_failed`。本地隔离测试可使用短期 HS256 测试密钥，生产必须由 Secret Manager 提供轮换验证材料。 |
| `PIXELFLOW_TOOL_BROKER_JWT_ISSUER` | Gateway 接受的 Sidecar 服务 JWT 签发方。 | 默认 `pixelflow-harness-sidecar`；不匹配即 401。变更后需重启，只影响新请求。 |
| `PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE` | Gateway 接受的 Sidecar 服务 JWT 受众。 | 默认 `pixelflow-tool-broker`；不匹配即 401。变更后需重启，只影响新请求。 |
| `PIXELFLOW_HARNESS_SIDECAR_BASE_URL` | Gateway 调用 Sidecar 的内部地址。 | 必填时装配真实 `GatewayHarnessRunBridge`；生产默认必须 HTTPS，仅允许本机 `http://127.0.0.1:<port>` 或同一受控 Docker Compose 网络中的固定值 `http://harness-sidecar:8090`。缺失时不装配新 Run Bridge，不会回退为 Fake。 |
| `PIXELFLOW_GATEWAY_JWT_SIGNING_KEY` | Gateway 签发 Gateway→Sidecar 短期 JWT 的 HS256 密钥。 | 仅由 Secret Manager 注入；长度不足或缺失时不装配 Run Bridge。变更后需重启且只影响新请求。 |
| `PIXELFLOW_GATEWAY_INSTANCE_ID` | Gateway 实例身份。 | 写入 Gateway→Sidecar JWT 的 `service_instance_id`；为空时不装配 Run Bridge，避免跨实例调用不可审计。 |

该 Router 被全局终端用户 JWT 中间件精确排除，但不等于公开：只有
`/agent/internal/agent-tools` 本身及其子路径跳过终端用户认证，随后必须经过上表的
独立服务 JWT 校验。JWT 必须包含 `exp`、`iat`、`iss`、`aud` 和非空
`service_instance_id`。浏览器、用户 Authorization 和公开 OpenAPI 均不能访问该接口。

## `agent-tools-v1` Manifest 字段映射

| 字段 | 用途与影响 |
| --- | --- |
| `protocol_version` | 固定协议版本；非 `v1` 请求由 DTO 拒绝，防止 Sidecar/Gateway 合同漂移。 |
| `version` | Tool 集版本；Run 接受时冻结，后续发布不热切运行中 Run。 |
| `digest` | 对规范化 Tool 列表计算的 SHA-256；与 Run binding 不一致时 Broker fail-closed。 |
| `tools[].name` | 稳定 Tool 名；当前只允许 `inspect_video_workspace`。 |
| `tools[].description` | 面向模型的中文业务说明；不携带 owner、URL、Provider 原始字段或凭据。 |
| `tools[].parameters_schema` | 严格 JSON Schema；当前工具为无参数对象，工作区归属只能来自 Run binding。 |
| `tools[].cost_level` | 费用等级；当前为 `none`，不允许通过此字段绕过未来计费确认。 |
| `tools[].confirmation_required` | 是否要求人工确认；当前只读 Tool 为 `false`。 |
| `tools[].idempotency_mode` | 幂等策略；当前为 `read_only`，仍须携带稳定 `Idempotency-Key`。 |
| `tools[].recovery_mode` | 恢复策略；当前为 `inline`，图片/视频生成由 Gateway GenerationJob Worker 处理。 |
| `tools[].workspace_mutation_roots` | 可修改的 Workspace 根字段白名单；当前为空，任何写入都不在此 Tool 的权限内。 |

`POST /calls` 的 `Idempotency-Key` 必须等于
`SHA-256(run_id + ":" + tool_call_id)`，而不是任意客户端生成值。Gateway 只按持久化的
`run_id` binding 回查用户、对话和工作区；不会信任 Sidecar 传入的 owner、workspace 或 revision。
