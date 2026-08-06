# PixelFlow Conversation Agent 合同 v1

> 这是两条并行开发线之间的冻结边界。字段变化必须先更新本文件、JSON fixture 和合同测试，再改实现。

## 1. 运行时归属

每个对话创建后固定一个编排模式。目标实现中它是服务端拥有的 conversation 列（或等价的不可被普通 context PATCH 覆盖的字段），不是前端可随意修改的 context key：

```json
{
  "orchestration_mode": "frontend_v2 | supervisor_v1",
  "orchestration_version": 1
}
```

- 历史对话和已有 pending job 的对话默认 `frontend_v2`。
- 新对话才允许按已批准的阶段配置选择 `frontend_v2` 或 `supervisor_v1`；当前各阶段发布范围为全部新对话100%，历史对话不迁移。
- 有 pending job 的 `frontend_v2` 对话禁止迁移，避免前后端同时启动收费任务。
- `supervisor_v1` 对话的前端不得再调用现有图片、视频、PPT `/start` 接口启动下一阶段；只能提交 turn 或人工决策。

## 2. Supervisor 决策合同

```text
ActionDecision
├─ action
├─ intent
├─ target_workflow_id
├─ target_stage
├─ target_artifact_ref
├─ confidence
├─ requires_confirmation
├─ clarification_question
├─ patch
├─ reason_code
└─ idempotency_key
```

`action` 只允许以下值：

| action | 含义 | 是否允许改变业务状态 |
| --- | --- | --- |
| `answer_only` | 解释、询问、比较，不推进流程 | 否 |
| `continue_workflow` | 回答当前 interrupt、确认或继续下一步 | 是 |
| `modify_workflow` | 修改当前任务的需求、Plan、场景或产物 | 是 |
| `regenerate_stage` | 重新生成当前明确阶段 | 是，通常可能计费 |
| `retry_failed` | 重试明确失败的 job/stage | 是，可能计费 |
| `start_workflow` | 在本对话中新建业务任务 | 是 |
| `switch_workflow` | 将交互焦点切到已有任务，不隐式取消原任务 | 是 |
| `cancel_workflow` | 取消或终止明确任务 | 是 |
| `clarify` | 目标或动作不确定，先向用户追问 | 否 |

`intent`：`image | video | ppt | video_analysis | general`。

约束：

- `reason_code` 只能是简短、可审计的规则码，不存储模型思维链。
- 涉及新计费调用且目标不唯一时必须 `clarify`，不能按低置信度猜测。
- `reply_to_message_id`、`artifact_refs`、场景 `@mention` 的明确指向优先于“最近一个任务”。
- `answer_only` 不得悄悄修改 `current_stage`、Plan 或 pending job。

## 3. 会话、任务和运行记录

### 3.1 WorkflowRecord

```json
{
  "workflow_id": "wf_...",
  "conversation_id": "conv_...",
  "kind": "image | video | ppt | video_analysis",
  "status": "draft | awaiting_user | running | paused_quota | failed | completed | cancelled",
  "current_stage": "string",
  "stage_version": 1,
  "creation_contract_snapshot": {},
  "pending_external_job": null,
  "latest_artifact_refs": [],
  "context_version": 1,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### 3.2 TurnRecord

一个用户输入对应一个 turn。网络重试必须通过 `conversation_id + client_input_id` 幂等返回同一 turn。

```json
{
  "turn_id": "turn_...",
  "conversation_id": "conv_...",
  "client_input_id": "uuid-from-client",
  "status": "accepted | queued | processing | waiting_user | completed | failed",
  "target_workflow_id": null,
  "decision": null,
  "expected_context_version": 12,
  "created_at": "ISO-8601"
}
```

### 3.3 ExternalJobRef

```json
{
  "job_id": "agent-owned-id",
  "provider_job_id": "existing-v2-job-id",
  "workflow_id": "wf_...",
  "stage": "image_generate",
  "status": "created | polling | succeeded | failed | timeout | expired",
  "attempt": 1,
  "idempotency_key": "stable-key",
  "next_poll_at": "ISO-8601",
  "lease_owner": null,
  "lease_expires_at": null
}
```

刷新、SSE 重连和恢复只查询该记录，不重新调用供应商 `/start`。

## 4. ContextEnvelope 与摘要

模型每次只收到组装后的 `ContextEnvelope`，而不是数据库里所有原始消息：

```text
ContextEnvelope
├─ current_input                         # 当前用户原文，不压缩
├─ active_or_target_workflow             # 当前目标任务结构化状态
├─ recent_messages                       # 最近原始消息窗口
├─ conversation_summary                  # 对话级结构化摘要
├─ related_workflow_summaries             # 其他任务摘要，不塞完整产物
├─ relevant_long_term_memories            # PowerMem 检索结果
├─ artifact_evidence_refs                 # 消息/产物引用与必要片段
├─ unresolved_questions                   # 尚未回答的问题
└─ budget_report                          # 估算 token 与采用的压缩等级
```

`ContextSummary` 必须结构化保存：

- 用户目标与关键需求；
- 已确认决策；
- 否定约束和禁止项；
- 每个 workflow 的状态摘要；
- 当前待确认问题；
- artifact/message 证据引用；
- 覆盖的消息范围、上版摘要 ID、版本、内容 hash；
- 压缩模型和时间，不保存隐藏思维链。

原始消息和原始 artifact 永久作为事实来源保留。压缩只改变“下一次给模型看的上下文”，不能删除前端历史或覆盖权威 Plan/创作合同。

## 5. 上下文预算合同

上下文窗口来自“本次实际调用模型的能力档案”，不是 LangGraph 自己提供的固定窗口。LangGraph/checkpointer 负责保存状态；模型供应商限制才决定单次 LLM 可输入多少。

统一计算：

```text
effective_context = min(模型已验证的 max_context_tokens, context_budget.effective_context_k × 1024)
usable_input = effective_context - max_output_tokens - safety_reserve_tokens
utilization = estimated_input_tokens / usable_input
```

R1 修复后的统一合同如下，`K` 固定表示 `1024 tokens`：

| 适用范围 | 有效窗口 | 输出预留 | 安全预留 | 可用输入 |
| --- | ---: | ---: | ---: | ---: |
| Supervisor、图片/编辑、视频、PPT、视频分析、摘要节点以及未来新增 Agent/流程 | 896K（917,504） | 32K（32,768） | 32K（32,768） | 832K（851,968） |

这四个预算值来自 `pixelflow.agent_runtime.context_budget`，代码不得再维护节点常量表。修改 YAML 后重启，所有当前和未来 Agent 节点自动采用新值；模型物理上限仍由 `models[].context_profile.max_context_tokens` 约束。当前 `deepseek-v4-pro` 的档案按已确认的 `1,000,000 tokens` 配置，因此还保留 `82,496 tokens` 物理余量。

模型档案至少配置：`max_context_tokens`、`max_output_tokens`、`tokenizer_strategy`、`verified_at`、`source`。实际 PixelFlow 流程必须保持 `require_verified_model_profile=true`：缺失、未验证、未来时间或过期档案一律 fail-closed，不得走 128K。`profiles.py` 中的 128K 仅作为底层兼容解析和显式非严格单元测试能力，不是 R1–R4 的运行兜底。

压缩阈值针对 `usable_input`：

| 利用率 | 动作 |
| ---: | --- |
| 60% | 非 LLM 清理：大型 tool 输出外置，只保留摘要、状态和 artifact 引用 |
| 72% | 标准增量压缩：旧消息进入结构化 conversation/workflow summary |
| 85% | 分层压缩：workflow 摘要再汇总为会话摘要，保留证据指针和最近原文 |
| 92% | LLM 调用前硬闸门：同步压缩；失败则使用最小安全上下文或暂停，不允许直接超窗请求 |

成功压缩目标是回落到 45% 以下。阈值按全局 Context Runtime 配置，所有现有和未来 workflow 自动继承，业务节点不得自己实现另一套压缩百分比。

失败恢复使用 `pixelflow.agent_runtime.compaction_retry_backoff_seconds`。当前为 30 秒；失败事务必须持久化 `retry_not_before`，Snapshot、SSE 和 Run 轮询在退避期内只读状态，到期后才允许单次恢复。恢复期间的新输入继续原子入队，成功后按顺序执行，前端不得重发。

## 6. 前端可感知事件合同

统一事件 envelope：

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "sequence": 42,
  "cursor": "opaque-cursor",
  "conversation_id": "conv_...",
  "run_id": "run_...",
  "occurred_at": "ISO-8601",
  "type": "context.compression_started",
  "payload": {}
}
```

首批事件：

- `run.state_changed`
- `context.compression_started`
- `context.compression_progressed`
- `context.compression_completed`
- `context.compression_failed`
- `input.state_changed`
- `message.upserted`
- `workflow.progressed`
- `interrupt.opened`
- `interrupt.closed`
- `external_job.state_changed`
- `external_job.quota_state_changed`
- `agent.route.decided`
- `error.raised`

规定：

- 事件先持久化再发送，SSE 只做投递；断线后用 cursor 补发。
- `message.upserted` 携带已经持久化的消息响应，artifact 继续放在现有 `payload.artifact`，避免消息和 artifact 两套事件乱序。
- 前端按 `event_id + sequence` 幂等消费。发现 sequence gap 时重新取 snapshot，不自行猜状态。
- `agent.route.decided`只携带公开`RouteDecision`和冻结后的`orchestration_mode`，不得包含Prompt、模型原始响应、推理过程或异常正文。
- 压缩提示属于运行时状态，不写成聊天消息，也不向用户展示摘要正文、token 数或内部 prompt。

建议文案：

- 开始：`对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。`
- 完成：`上下文整理完成，正在继续处理刚才的请求。`
- 可恢复失败：`上下文整理暂时未完成，你的输入已保留，系统将继续重试。`

压缩期间输入框仍可编辑、上传和发送。后端把新输入持久化为 `queued`；前端只显示排队状态，绝不自动重发。

## 7. API 合同

所有新接口继续使用 `/agent` 前缀：

| API | 作用 |
| --- | --- |
| `POST /agent/conversations/{id}/turns/start` | 原子保存用户输入并创建/复用 turn 与 run |
| `GET /agent/conversations/{id}/agent-snapshot` | 恢复 run、压缩、队列、消息、workflow、interrupt 和 cursor |
| `GET /agent/conversations/{id}/agent-events?cursor=...` | SSE 订阅和断点续传 |
| `POST /agent/conversations/{id}/interrupts/{interrupt_id}/responses` | 提交方向选择、表单、Plan 同意、修改、结束等人工响应 |
| `GET /agent/conversations/{id}/turns/jobs/{run_id}` | SSE 不可用时的 run 轮询兜底 |

`POST turns/start` 请求至少包含：

```json
{
  "client_input_id": "uuid",
  "content": "用户原文",
  "materials": [],
  "reply_to_message_id": null,
  "artifact_refs": [],
  "expected_context_version": 12
}
```

服务端必须先持久化输入并返回 `accepted/queued`，再异步执行 Supervisor。超时不能让前端重新创建 turn。

`client_input_id` 是新运行时的统一幂等键。保存可见用户消息时，Repository Adapter 将它映射到现有消息 payload 的 `client_message_id` 语义；不要求前端为同一次输入生成两个 UUID。

## 8. LangGraph/checkpointer 命名

- Supervisor thread：`pf:conversation:{conversation_id}:supervisor:v1`。
- Workflow thread：`pf:conversation:{conversation_id}:workflow:{workflow_id}:v1`。
- 父 Supervisor 保存会话级判断；每个 workflow 独立保存可恢复状态，允许同一对话存在多个已完成/暂停任务。
- 子图默认按一次命令继承 checkpointer；需要多轮人工确认的 workflow 使用稳定 workflow thread 恢复。
- SQL 业务投影是查询、幂等、列表和 UI snapshot 的权威来源；checkpointer 是图执行位置的权威来源。两者通过 workflow/turn/run ID 对齐，不能把二进制产物复制进 checkpoint。

## 9. 兼容性不变量

- 创意方向和 Plan 继续人工确认。
- 图片结果 60 秒默认满意；视频结果必须人工结束；视频场景包无倒计时确认。
- 关闭图片/视频/PPT 表单视为取消并记录 `form_cancelled`。
- 图片编辑缺原图时暂停等待上传；失败重试重新打开参数确认。
- 视频创作合同、Plan、资产清单、场景包保持权威继承；单分镜 4–15 秒。
- 刷新、切换对话和网络重试不重复启动计费任务。
- 额度不足进入 `paused_quota`，充值后可从原状态恢复。
- 下载才完成“导出交付”；预览不算下载。
- content-app Authorization 只透传，不进入摘要、事件、日志或 PowerMem。
- PowerMem 继续只通过 `PowerMemService` helper 读写；上下文压缩不替代长期语义记忆。

## 10. 官方机制依据

- LangGraph persistence 通过 `thread_id` 和 checkpointer 保存每个步骤状态，可用于恢复、人工介入和容错：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- `interrupt()` 会保存图状态并等待外部输入；恢复前的副作用必须幂等：[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。
- 子图是否跨调用保留状态由 checkpointer 模式决定；父图必须有 checkpointer 才能支持子图持久化和 interrupt：[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)。
- 官方建议对长对话进行消息裁剪或摘要，并把运行摘要作为图状态的一部分：[Memory](https://langchain-ai.github.io/langgraph/how-tos/memory/manage-conversation-history/)。

仓库已有 `DeerFlowSummarizationMiddleware` 和 `SummarizationConfig`，因此不采用来源不明的第三方“压缩 Skill”。本方案复用其 token 计数、摘要和 skill 内容保护思路，但增加 PixelFlow 必需的持久化结构化摘要、压缩锁、输入排队和前端事件；现有 middleware 作为最后一道防溢出保护，而不是唯一的业务压缩实现。
