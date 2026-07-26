# M13.1 / R1 自动上下文压缩恢复修复设计

## 目标

修复 R1 真实视频流程中出现的两个共享 Runtime 问题：

1. 前端恢复专用的完整 Plan 修订请求重复进入模型业务上下文，导致最小安全上下文仍超过模型可用输入上限。
2. 压缩失败后把 `retry_required` 租约到期时间写成当前时刻，Snapshot、SSE 和 Turn 轮询会连续触发无退避恢复。

修复后必须保留现有 Conversation Store 的刷新恢复能力、Turn 幂等、压缩期间输入排队、失败关闭和旧 v2 业务接力语义。

## 实测根因

测试对话 `8c47adc01b3844068b0de652d9e486c1` 在视频 plan.md 审核阶段点击“Agent 修改”后，同时持久化了：

- `pendingPlanRevisionRequest`
- `pending_plan_revision_request`

两个字段分别约为 `122,448 bytes`，内容相同，均属于前端刷新恢复 DTO。`_business_context()` 已过滤图片、视频、Plan 和 PPT 大纲的旧修订快照，但没有过滤这两个 Plan 修订请求字段，因此它们被当作不可压缩业务事实重复送入上下文预算。

在该真实对话上：

- 修复前业务上下文估算值约为 `307,110 tokens`。
- 排除两个恢复专用字段后约为 `66,687 tokens`。
- 当前模型使用保守档案时，Supervisor 可用输入上限为 `90,112 tokens`。
- 修复前最小安全上下文不可能低于上限；修复后业务权威上下文可以保留在上限内。

第二个根因位于压缩收尾和恢复唤醒之间：

1. 压缩暂停或异常时，Repository 写入 `retry_required`。
2. 同一事务把 `lease_expires_at` 写成当前时刻。
3. Snapshot、SSE 事件读取和排队 Turn 轮询都把“已经到期”解释为可立即恢复。
4. 恢复再次失败后又写入已经到期的租约，形成由页面轮询持续驱动的重试循环。

## Token 数值与动态预算

`307,110` 和 `66,687` 不是配置常量，而是 `estimate_context_tokens()` 对某一时刻真实 Conversation、消息、Workflow 和业务上下文的动态估算结果。用户继续输入、Plan 版本变化、场景包增加或恢复字段变化都会改变这些值。

`90,112` 也不是直接写死的业务上限，而是当前模型档案与 `supervisor` 节点预算策略按以下公式计算：

```text
effective_context_tokens =
  min(model_profile.max_context_tokens, supervisor.effective_context_cap_tokens)

max_output_tokens =
  min(model_profile.max_output_tokens, supervisor.output_reserve_tokens)

usable_input_tokens =
  effective_context_tokens - max_output_tokens - supervisor.safety_reserve_tokens
```

当前默认模型没有有效且未过期的 `context_profile`，因此 Runtime 使用内建保守档案：

```text
effective_context_tokens = 128 × 1024 = 131,072
max_output_tokens = 8 × 1024 = 8,192
safety_reserve_tokens = 32 × 1024 = 32,768
usable_input_tokens = 131,072 - 8,192 - 32,768 = 90,112
```

压缩等级按 `usable_input_tokens` 的 `60% / 72% / 85% / 92%` 动态判定；严格压缩目标按可用输入的 45% 计算。当前 `90,112` 对应的严格目标为 `40,550 tokens`。92% 最小安全上下文允许在无法达到 45% 时退化到“仍低于可用输入上限”的安全结果，但不得放行超过 `90,112` 的输入。

以后扩大 Agent 模型上下文窗口时，只有同时满足以下条件才会提高预算：

1. 在 `models[].context_profile` 中声明真实模型能力。
2. `verified_at`、`source` 和可选 `expires_at` 构成有效且未过期的验证证据。
3. 声明值不超过对应节点的 `effective_context_cap_tokens`；Supervisor 当前最高仍受 `256 × 1024` 限制。
4. 修改配置后重启服务，使启动快照重新装配模型档案。

扩大窗口不会改变 Conversation、Turn、队列或旧 v2 工作流合同，但会推迟自动压缩触发时间，使单次模型输入更大，并可能增加模型调用延迟、费用和供应商拒绝超窗请求的风险。不得只按供应商宣传值放大配置；必须先验证实际模型窗口和最大输出能力。模型档案属于 Runtime 启动配置，重启后历史对话的下一次 Turn 也会使用新预算，不只影响新建对话。

## 方案比较

### 方案一：共享上下文投影过滤加持久化固定退避

保留 Conversation Store 中全部兼容字段，只在模型业务上下文投影中排除恢复专用修订数据；失败收尾使用现有 `lease_expires_at` 表示“下次允许恢复时间”，默认固定退避 30 秒。三个读取入口只在租约到期后唤醒同一个进程内单飞恢复任务。

优点是修复根因、不改变数据库结构、不破坏刷新恢复，图片、视频、PPT 和视频分析共用同一 Runtime 行为。缺点是固定退避不记录连续失败次数。

### 方案二：增加重试次数和独立下次重试字段

为压缩协调表增加 `retry_count` 和 `next_retry_at`，实现指数退避和最大次数。

该方案的长期治理能力更强，但需要数据库迁移、合同变化、Memory/SQL 双实现和旧数据兼容，超过本次已确认的最小修复范围。

### 方案三：只限制前端轮询

前端看到失败提示后降低 Snapshot/SSE/Turn 查询频率。

该方案不能阻止其他客户端、刷新或服务端调用触发同一问题，也不能解决超大恢复 DTO 进入模型上下文，因此不采用。

本次采用方案一。

## 组件与职责

### 业务上下文投影

`backend/pixelflow/agent_runtime/runtime_compaction.py` 的业务上下文投影负责区分：

- Conversation Store 权威恢复数据：继续完整保留，供前端刷新、离开和重新进入时恢复。
- 模型业务上下文：排除仅用于 UI 恢复且已经由稳定业务字段表达的完整修订快照或修订请求。

恢复专用键集合需要覆盖现有图片、视频、Plan、PPT 大纲修订快照，以及 Plan 修订请求的 camelCase 和 snake_case 兼容字段。过滤只作用于深拷贝后的模型投影，不修改 Store 原值。

### 压缩失败退避

`ConversationCompactionRuntime` 增加默认 30 秒的 `retry_backoff`。压缩暂停、进度事件失败或执行异常时，终态事件仍按原合同写入 `context.compression_failed`，同时把 `retry_required` 的 `lease_expires_at` 设置为：

```text
failure_time + retry_backoff
```

Repository 的 `finish_compaction()` 和 `finish_compaction_with_event()` 增加明确的 `retry_not_before` 参数。`claim_next=false` 时必须校验该时间晚于本次收尾时间；Memory 和 SQL 实现保持相同语义。`claim_next=true` 时不得传入重试时间。

### 恢复唤醒

Snapshot、SSE 事件读取和排队 Turn 轮询继续作为轻量恢复触发器，但必须先读取持久化租约：

- 无租约：不恢复。
- `lease_expires_at > now`：处于运行期或退避期，不创建恢复任务。
- `lease_expires_at <= now`：允许创建一次按用户和对话去重的进程内恢复任务。

恢复任务仍使用原 Turn、原 `client_input_id` 和稳定消息 ID，不允许前端重新发送。

## 跨流程影响

R1 的上下文预算、压缩协调、Turn 队列、Snapshot 和 SSE 是图片、视频、PPT、视频分析共同使用的会话基础设施。本次过滤和退避修复会同时作用于四类流程。

这不等于可以静态保证未来所有业务上下文都不会超窗。新增 Agent、Workflow、Artifact 或恢复 DTO 时，仍必须判断字段属于：

- 必须进入模型的权威业务事实；
- 只供 Store/UI 恢复的完整载荷；
- 可以外置并用稳定引用代替的大型载荷。

跨流程验收必须用代表性上下文验证每类流程的最小安全上下文低于其实际节点可用输入上限。

## 测试策略

严格按 TDD 分两轮执行。

第一轮验证业务上下文投影：

1. 先增加包含真实体量 Plan 修订请求的失败测试。
2. 断言预算投影不包含恢复专用字段，Store 读取仍完整保留 camelCase 和 snake_case 数据。
3. 断言修复前测试超过 `90,112`，修复后最小安全上下文低于该上限。
4. 用图片、视频、PPT、视频分析代表性恢复字段做参数化回归。

第二轮验证重试退避：

1. 先把现有“失败后立即恢复”测试改为“退避期内多次 Snapshot、SSE、Turn 轮询均不产生新压缩事件”并观察失败。
2. 推进测试时钟到 30 秒边界后，只允许一次恢复。
3. 同时覆盖 Memory 和 SQL Repository 的 `retry_not_before` 校验、持久化和 fencing 语义。
4. 保留成功压缩后领取队首、失败时全部 Turn 继续排队、刷新不重复消息的既有回归。

完成自动化测试后，使用本机 Chrome 重新打开原测试对话或创建等价 30 秒视频对话，验证：

- 自动压缩产生一次 started/progressed/completed 序列。
- 压缩期间的新输入显示稳定队列位置。
- 成功后队首进入 processing 并继续旧 v2 视频流程。
- 刷新或重新进入不重复保存消息。
- 退避窗口内事件数量保持不变。

真实复测继续停在 plan.md 或场景包确认之前，不调用付费图片、视频或 PPT 生成接口。

## 文档与交付

实现完成后同步更新：

- `docs/pixelflow-agent-skill-flow-latest-design.md`
- 中文测试结论和交接记录

本次不修改生产配置，不扩大模型上下文窗口，不改变 `assist / enabled_intents=[] / 100%` 的 R1 测试候选边界，不提升 `automation_local_ready` 状态。
