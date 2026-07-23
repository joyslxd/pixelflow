# Agent Runtime 配置说明

以下配置只在 Gateway 进程启动时读取，修改后必须重启。当前 M00 的安全默认值固定为 `off + [] + 0 + false`，不会接管现有 v2 对话，也不会调用真实付费 API。

| YAML 路径 | 环境变量 | 类型、默认值与范围 | 用途与开启影响 | 回滚 |
| --- | --- | --- | --- | --- |
| `pixelflow.agent_runtime.mode` | `PIXELFLOW_AGENT_RUNTIME_MODE` | 字符串；默认 `off`；可选 `off/shadow/assist/primary` | 用途：选择新会话 Runtime 模式。影响：`off` 保持现有 v2；其他值只允许在对应发布阶段获批后使用。 | 改回 `off` 并重启，只影响之后新建的对话。 |
| `pixelflow.agent_runtime.enabled_intents` | `PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS` | 字符串数组；默认 `[]`；元素只能是 `video/image/ppt/video_analysis` | 用途：限制 `primary` 可接管的业务类型。影响：空数组不允许任何业务进入 `primary`。 | 改回 `[]` 并重启，不迁移历史或运行中对话。 |
| `pixelflow.agent_runtime.new_conversation_rollout_percent` | `PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT` | 整数；默认 `0`；范围 `0–100`，单位百分比 | 用途：控制多少新建对话进入新 Runtime。影响：`0` 表示全部新对话继续使用现有 v2。 | 改回 `0` 并重启，只阻止后续新对话进入。 |
| `pixelflow.agent_runtime.context_compaction_enabled` | `PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED` | 布尔值；默认 `false` | 用途：控制新 Runtime 上下文压缩。影响：`false` 不启动压缩流程；`true` 仅在 Runtime 已获批启用时生效。 | 改回 `false` 并重启，不删除已有消息或产物。 |

这些键不包含 token、密钥或账号。非法模式、intent、比例或布尔值会在 Gateway 导入路由前阻止进程启动，避免部分启用。
