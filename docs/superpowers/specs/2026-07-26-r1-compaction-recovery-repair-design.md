# M13.1 / R1 统一上下文预算与压缩恢复修复设计

## 目标

本次同时完成一项设计升级和两个真实缺陷修复：

1. DeepSeek V4 Pro 经用户确认具有 `1,000,000 tokens` 物理上下文窗口。PixelFlow 所有现有和未来 Agent 节点统一使用配置驱动的 `896K` 业务有效窗口，不再维护 Supervisor、图片、视频、PPT、视频分析和摘要节点各自不同的窗口上限。
2. 修复前端恢复专用的完整 Plan 修订请求重复进入模型业务上下文，导致最小安全上下文仍超过模型可用输入上限的问题。
3. 修复压缩失败后立即到期，Snapshot、SSE 和 Turn 轮询连续触发无退避恢复的问题。

修复后必须保留 Conversation Store 刷新恢复、Turn 幂等、压缩期间输入排队、失败关闭和旧 v2 业务接力语义。配置调整必须成为唯一预算来源：后续只修改 dev/prod 配置并重启，所有现有和未来 Agent 节点都使用新值，不需要修改 Python 节点常量。

## 实测根因

测试对话 `8c47adc01b3844068b0de652d9e486c1` 在视频 plan.md 审核阶段点击“Agent 修改”后，同时持久化了：

- `pendingPlanRevisionRequest`
- `pending_plan_revision_request`

两个字段分别约为 `122,448 bytes`，内容相同，均属于前端刷新恢复 DTO。`_business_context()` 已过滤图片、视频、Plan 和 PPT 大纲的旧修订快照，但没有过滤这两个 Plan 修订请求字段，因此它们被当作不可压缩业务事实重复送入上下文预算。

在旧 128K 保守档案下，该真实对话的历史测量结果是：

- 修复前业务上下文估算值约为 `307,110 tokens`。
- 排除两个恢复专用字段后约为 `66,687 tokens`。
- 旧 Supervisor 可用输入上限为 `90,112 tokens`。

这些数字只用于证明重复载荷根因，不是新设计常量。扩大窗口不能替代该修复：不排除恢复专用字段会持续浪费预算，并在未来更长对话中重新触发同类失败。

第二个根因位于压缩收尾和恢复唤醒之间：

1. 压缩暂停或异常时，Repository 写入 `retry_required`。
2. 同一事务把 `lease_expires_at` 写成当前时刻。
3. Snapshot、SSE 事件读取和排队 Turn 轮询都把“已经到期”解释为可立即恢复。
4. 恢复再次失败后又写入已经到期的租约，形成由页面轮询持续驱动的重试循环。

## 统一预算合同

### 两层窗口

模型物理能力和业务预算必须分开：

- `models[].context_profile.max_context_tokens=1000000`：记录 DeepSeek V4 Pro 经确认的物理硬上限。
- `pixelflow.agent_runtime.context_budget.effective_context_k=896`：记录 PixelFlow 主动采用的业务有效窗口。

仓库延续现有二进制 K 约定，所有 `*_k` 配置都按 `1K = 1024 tokens` 转换。统一计算公式为：

```text
effective_context_tokens =
  min(model_profile.max_context_tokens, context_budget.effective_context_k × 1024)

max_output_tokens =
  min(model_profile.max_output_tokens, context_budget.output_reserve_k × 1024)

safety_reserve_tokens =
  context_budget.safety_reserve_k × 1024

usable_input_tokens =
  effective_context_tokens - max_output_tokens - safety_reserve_tokens
```

本次确认值为：

```text
模型物理上限 = 1,000,000 tokens
业务有效窗口 = 896K = 917,504 tokens
输出预留 = 32K = 32,768 tokens
安全预留 = 32K = 32,768 tokens
可用输入 = 832K = 851,968 tokens
模型物理余量 = 1,000,000 - 917,504 = 82,496 tokens
```

压缩等级继续按可用输入的 `60% / 72% / 85% / 92%` 判定；严格压缩目标继续按 45% 计算。当前配置的严格目标为 `383,385 tokens`。所有比例针对运行时转换后的 `usable_input_tokens` 动态计算，不在节点里保存派生常量。

### 配置结构

dev 和 prod 使用相同结构与数值：

```yaml
pixelflow:
  agent_runtime:
    context_budget:
      effective_context_k: 896
      output_reserve_k: 32
      safety_reserve_k: 32
      require_verified_model_profile: true
    compaction_retry_backoff_seconds: 30

models:
  - name: "deepseek-v4-pro"
    context_profile:
      max_context_tokens: 1000000
      max_output_tokens: 32768
      tokenizer_strategy: "conservative_estimate"
      verified_at: "2026-07-26T00:00:00+08:00"
      source: "AIRouter DeepSeek V4 Pro 能力确认"
```

实际提交时，每一个新增或修改的 YAML 叶子配置项都必须有紧邻中文注释，说明用途、影响、单位、默认值、合法范围、重启要求、生效对象和回滚方式；敏感值说明不得包含真实凭据。

`config.dev.yml` 保持 `assist / enabled_intents=[] / 100% / context_compaction_enabled=true`，用于本机真实验证。`config.prod.yml` 同步统一预算和模型档案，但生产 `mode=off` 保持不变；写入预算不会自动启用生产 Runtime。

### 严格模型档案

`require_verified_model_profile=true` 时，参与 Context Runtime 的实际模型必须具有：

- `max_context_tokens`
- `max_output_tokens`
- `tokenizer_strategy`
- `verified_at`
- `source`

档案缺失、未经验证、验证时间位于未来或已经过期时，Context Runtime 必须 fail-closed，拒绝启动压缩组件并返回不含敏感信息的配置错误。实际 dev/prod 流程不得再静默使用 128K 保守档案。

底层 `resolve_model_context_profile()` 可以继续保留 128K 兼容结果，供未接入严格 Runtime 的旧调用和合同测试使用；配置启用严格模式后，装配层必须拒绝该 fallback 状态，因此所有 PixelFlow Agent 流程不会实际走到 128K。

## 方案比较

### 方案一：单一全局预算加严格模型档案

把统一 K 值放在 `pixelflow.agent_runtime.context_budget`，把模型物理能力放在 `models[].context_profile`。启动时生成一个不可变 `ContextBudgetPolicy`，Supervisor、图片、图片编辑、视频、PPT、视频分析、摘要以及任意未来节点都从同一个 Provider 获取该对象。

优点：

- 配置是唯一预算来源。
- 新增节点不会因为忘记添加映射而回到旧窗口。
- 模型物理能力和业务保护余量职责清晰。
- 缺失档案时 fail-closed，不会伪装成 896K。

本次采用该方案。

### 方案二：保留分节点配置并统一默认值

可以把现有各节点常量都改成 896K，但未来新增节点仍可能漏配或重新产生差异，不符合“所有现有和未来 Agent 节点统一”的目标，因此不采用。

### 方案三：把 128K fallback 常量直接改成 896K

改动最小，但未知模型也会被当作 896K，且无法区分模型硬上限和业务有效窗口。该方案会把安全错误变成供应商超窗错误，因此不采用。

## 组件与职责

### 配置绑定

`AgentRuntimeConfig` 增加不可变的嵌套 `ContextBudgetConfig`：

- `effective_context_k`
- `output_reserve_k`
- `safety_reserve_k`
- `require_verified_model_profile`

同时增加 `compaction_retry_backoff_seconds`。Profile loader 负责把 dev/prod YAML 的每个叶子项映射为进程启动配置，并拒绝未知字段、空值、布尔值冒充整数、非正数以及：

```text
output_reserve_k + safety_reserve_k >= effective_context_k
```

预算对象在 Gateway 启动时冻结。配置文件修改后必须重启服务；重启后新对话和历史对话的下一次 Turn 都使用新预算，运行中的单次压缩任务不热切换。

### 统一预算 Provider

现有 `_CONTEXT_BUDGET_POLICIES` 分节点映射改为单一配置驱动 Provider。`get_context_budget_policy(node)` 继续接收节点名用于日志和审计，但不再按名称选择不同数值。任意非空未来节点名都返回同一份冻结预算。

`ContextBudgetGuard`、`ContextAssembler` 和 `ContextCompactionCoordinator` 必须通过构造参数接收同一个 Provider，不读取模块级可变全局变量。生产装配只创建一次 Provider，避免不同组件读取到不同启动快照。测试可以注入小窗口策略，不需要修改环境变量。

### 业务上下文投影

`backend/pixelflow/agent_runtime/runtime_compaction.py` 的业务上下文投影负责区分：

- Conversation Store 权威恢复数据：继续完整保留，供前端刷新、离开和重新进入时恢复。
- 模型业务上下文：排除仅用于 UI 恢复且已经由稳定业务字段表达的完整修订快照或修订请求。

恢复专用键集合覆盖现有图片、视频、Plan、PPT 大纲修订快照，以及 Plan 修订请求的 camelCase 和 snake_case 兼容字段。过滤只作用于深拷贝后的模型投影，不修改 Store 原值。

### 压缩失败退避

`ConversationCompactionRuntime` 从配置读取 30 秒 `retry_backoff`。压缩暂停、进度事件失败或执行异常时，终态事件仍按原合同写入 `context.compression_failed`，同时把 `retry_required` 的 `lease_expires_at` 设置为：

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

## 实施顺序

扩大窗口只能推迟重复载荷和无限重试问题，不能修复根因，因此按以下顺序实施：

1. 先用失败测试固定 Plan 修订请求重复进入预算的问题，并修复业务上下文投影。
2. 再用失败测试固定 Snapshot、SSE、Turn 轮询重试风暴，并实现持久化 30 秒退避。
3. 然后用失败测试固定统一 896K 配置、严格模型档案和未来节点继承语义。
4. 最后更新 dev/prod 配置和 agentization 权威文档。

每个步骤都必须先观察测试因当前缺陷正确失败，再写最小实现使其通过。

## 跨流程影响与验证

R1 的上下文预算、压缩协调、Turn 队列、Snapshot 和 SSE 是图片、视频、PPT、视频分析共同使用的会话基础设施。本次统一配置和缺陷修复会同时作用于四类流程。

这不等于可以静态保证未来任意业务载荷都不会超窗。新增或修改 Agent、Workflow、Artifact、附件或恢复 DTO 时，仍必须判断字段属于：

- 必须进入模型的权威业务事实；
- 只供 Store/UI 恢复的完整载荷；
- 可以外置并用稳定引用代替的大型载荷。

自动化验证包含：

1. 模型硬上限 `1,000,000`、统一业务窗口 `896K`、输出 `32K`、安全 `32K` 和可用输入 `832K` 的精确公式。
2. Supervisor、图片、图片编辑、视频、PPT、视频分析、摘要以及虚构未来节点全部返回相同预算。
3. 修改注入配置后，所有节点预算同步变化。
4. 严格模式下缺失、未验证、未来验证时间和过期模型档案均 fail-closed，不使用 128K。
5. 图片附件只保留稳定引用或外置载荷，当前输入的文本、materials、reply 和 artifact refs 保持完整。
6. 四类代表性长上下文均能触发压缩、稳定排队、成功领取队首、刷新不重复消息。
7. 失败后的退避期内重复 Snapshot、SSE 和 Turn 轮询不增加压缩事件；到期后只接管一次。
8. Memory 和 SQL Repository 保持相同租约、fencing 和队列语义。

## Chrome 真实验收

自动化通过后，启动当前真实分支 `feature/agent_0.8.4_boguan` 的 dev profile，并在用户本机 Chrome 中执行 30 秒视频流程：

1. 创建新视频对话并确认 Runtime 使用统一 896K 配置。
2. 完成视频表单、创意方向和 plan.md。
3. 构造足以触发压缩但不调用付费生成接口的历史消息或恢复载荷。
4. 压缩期间提交第二条输入，观察稳定队列位置。
5. 验证只产生一次 started/progressed/completed 序列。
6. 验证压缩成功后队首进入 processing，并继续旧 v2 视频流程。
7. 刷新或重新进入对话，确认不重复保存消息。
8. 使用受控失败场景验证 30 秒退避和单次恢复，不制造事件风暴。

浏览器验收停在 plan.md 或场景包确认之前，不调用付费图片、视频、PPT 或剪映生成接口。用户提供的 Authorization 只保存于本机浏览器调试入口，不写入代码、配置、日志、测试报告或文档。

其他流程采用真实形状但非付费的后端集成验证；如果共享 Runtime 验证失败，不能以视频流程通过替代跨流程结论。

## 文档与交付

实现完成后同步更新：

- `docs/agentization/contracts-v1.md`
- `docs/agentization/architecture-design.md`
- `docs/agentization/test-matrix.md`
- `docs/pixelflow-agent-skill-flow-latest-design.md`
- M13 状态、中文测试结论和交接记录

本次不启用生产 Runtime，不改变生产 rollout 或 `enabled_intents`，不提升 `automation_local_ready`，不调用真实付费生成接口。
