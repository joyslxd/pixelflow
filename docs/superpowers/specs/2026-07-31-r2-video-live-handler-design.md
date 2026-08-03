# R2 视频 live Handler 与真实 Supervisor 接线设计

## 1. 背景与结论

M02、M05、M06、M11、M12 和 M13.2 已把 Supervisor 合同、LangGraph 路由、视频领域服务、外部任务恢复、前端投影和发布门禁分别实现并集成到 `feature/agent_0.8.4_boguan`。但是当前 Gateway 仍使用空的 `FakeWorkflowRegistry`，`AgentRuntimeService.start_turn()` 只负责原子保存消息和 Turn、执行上下文压缩门禁，没有进程内执行器去领取真实 Turn 并驱动 Graph。测试中的 `_PlanningHandler` 也只是预先构造决策后的单服务替身，不是生产可注册的 live Handler。

因此，本切片不能仅把 `video` 字符串加入 `primary_execution_intents`。那样会把新视频对话切到 `supervisor_v1`，但没有完整业务执行和前端交互恢复能力，属于错误接管。

本设计批准的目标是：增加一个真实、可持久化恢复的 `SupervisorTurnExecutor` 和 `VideoLiveWorkflowHandler`，让 `supervisor_v1` 视频对话从 Turn 入队开始，经过动作解析、Validator、LangGraph、M11 视频服务和 M06 外部任务协调，最终把消息、Workflow、Artifact、Interrupt 与事件写回权威存储；同时改造前端，使 Supervisor 对话的表单、确认和修改动作全部提交结构化 Turn 或 interrupt response，不再调用拥有业务推进权的 v2 接口。

只有当 Gateway 完整装配并成功注册视频 Handler 时，`video` 才能进入 `primary_execution_intents`。任一依赖缺失时必须 fail-closed，继续保持 `frontend_v2`，不得形成“已接管但无法推进”的半启用状态。

## 2. 范围与非目标

### 2.1 本切片范围

- 为 `supervisor_v1` 增加可领取、执行、恢复 Turn 的运行时执行器。
- 实现视频 Workflow 的真实 Handler，覆盖 `AgentAction` 的九种动作。
- 为 M11 视频领域状态增加 Memory 与 SQL 两套完整状态仓储，而不是只保存 `WorkflowRecord` 查询投影。
- 增加 Turn 执行租约、fencing token、稳定幂等键和原子完成边界。
- 将 M11 视频服务和 M06 Operation/Outbox 恢复能力接到 Handler，保持供应商 start 的单次调用语义。
- 投影 `message.upserted`、`workflow.progressed`、`interrupt.opened`、`interrupt.closed`、`external_job.state_changed` 和安全错误事件，并让 Snapshot 能恢复真实 interrupt。
- 让 Supervisor 前端提交结构化动作、表单和 interrupt response，并从 Snapshot/SSE 恢复页面。
- 在 Gateway 中受控注册 `video` Handler；Handler 未完整装配时保持 v2 归属。
- 补齐单元、Memory/SQL 一致性、重启恢复、Gateway、前端和本地中文门禁测试。

### 2.2 明确不在本切片范围

- 不修改 `backend/config.prod.yml`，不执行 R2 生产发布。
- 不启用 `image`、`ppt` 或 `video_analysis` 的 primary 接管。
- 不调用真实付费供应商，不使用真实用户 Authorization、token 或密钥。
- 不实施 M13.3，也不执行 Agent→dev 合并。
- 不迁移历史对话和运行中任务；对话创建时冻结的 `orchestration_mode` 保持不变。
- 不用字符串模拟用户点击，不让模型隐藏推理进入持久层。
- 不把 v2 Controller 当成 Supervisor 的业务推进后门。

## 3. 总体架构

新增组件按 Java 后端类比可分为四层：

| 组件 | Java 类比 | 职责 |
| --- | --- | --- |
| `SupervisorTurnExecutor` | 消费队列的 Application Service | 领取 Turn、恢复上下文、生成/校验决策、执行 Graph、持久化结果和安排下一次恢复 |
| `VideoLiveWorkflowHandler` | 视频领域 Application Service 门面 | 把九种 `AgentAction` 映射为 M11 服务调用、人工确认 interrupt 或安全的只读答复 |
| `VideoWorkflowStateRepository` | Repository | 保存完整视频状态信封、CAS 版本和恢复元数据；同时维护精简的 `WorkflowRecord` 投影 |
| `SupervisorActionClient` | 前端 API Client | 把按钮、表单和修改意见编码为结构化 Turn/interrupt response，并读取 Snapshot/SSE |

运行链路如下：

```text
POST /messages/start
  -> 原子保存用户消息、Turn、input.state_changed
  -> SupervisorTurnExecutor 按租约领取 Turn
  -> 读取会话/Workflow/Artifact/Interrupt 权威快照
  -> 显式动作解析或 Supervisor 候选解析
  -> ActionDecision Validator
  -> LangGraph（稳定 namespace + checkpoint）
  -> VideoLiveWorkflowHandler
  -> M11 领域服务 / M06 Operation 与完成 Outbox
  -> 原子写入完整状态、Workflow 投影、消息、事件和 Turn 终态
  -> Snapshot/SSE 驱动前端恢复
```

## 4. Turn 执行器

### 4.1 领取条件

执行器只处理同时满足以下条件的 Turn：

- 对话冻结为 `orchestration_mode=supervisor_v1`；
- 对话 intent 为已成功注册的 `video`；
- Turn 状态为 `accepted` 或到期可恢复的 `queued`；
- 上下文未处于压缩重试退避边界；
- 同一对话没有更早的未完成 Turn；
- 没有有效的其他 worker 执行租约。

`frontend_v2` Turn 仍走现有 legacy handoff，执行器不得抢占。历史对话不会因为配置变化被重新归属。

### 4.2 租约与 fencing

仓储新增 Turn 执行租约：`lease_owner`、`lease_token`、`lease_expires_at`、`attempt` 和 `next_attempt_at`。领取必须用数据库条件更新或 Memory 临界区完成，返回单调变化的 fencing token。后续每次 Workflow 状态、事件和 Turn 终态写入都必须携带同一 token；租约过期后，旧 worker 即使稍后返回也不能提交。

领取不等于完成。执行器异常退出后不伪造失败、不释放他人租约；新进程只在租约过期后接管。可重试基础设施错误使用有上限的退避；合同错误、状态损坏和越权引用直接 fail-closed，写固定安全原因，不重放供应商 start。

### 4.3 决策来源

决策顺序固定为：

1. 优先读取前端提交的结构化动作载荷；
2. 若是自由文本，基于当前 Workflow、开放 interrupt、artifact 引用和会话消息构造候选；
3. 先执行确定性规则解析；只有存在多个合法候选或无法稳定判断时，才调用 Supervisor 分类模型；
4. 模型只返回 `ActionDecision` 公共字段，不保存 chain-of-thought；
5. 统一经过现有 Validator，再进入 LangGraph。

显式结构化动作仍必须经过 Validator。前端不能通过自行指定 `workflow_id`、`stage` 或 artifact 引用绕过用户/会话归属校验。

### 4.4 Graph 与幂等

Graph namespace 由 `user_id + conversation_id + workflow_id` 稳定派生，checkpoint 不使用进程内随机值。每个 Turn 使用 `conversation_id + client_input_id` 作为外层幂等身份；每个动作使用 Validator 产出的稳定 `ActionDecision.idempotency_key`；外部操作继续使用 M06 冻结的 `workflow_id + stage + stage_version + attempt` 身份。

进程在以下任意位置退出后都必须恢复原动作，而不是新建一次供应商调用：

- Turn 已领取、Graph 尚未 checkpoint；
- Graph 已 checkpoint、Handler 尚未完成；
- 供应商 start 已成功、`provider_job_id` 已落库；
- Provider 已终态、完成 Outbox 尚未投递；
- Workflow 状态已写入、前端投影事件尚未确认完成。

### 4.5 原子完成边界

一次 Turn 的本地状态提交至少包含：

- 完整视频 Workflow 状态的新版本；
- `WorkflowRecord` 查询投影；
- 助手消息或人工确认 interrupt；
- 对应事件 Outbox；
- Turn 的 `waiting_user`、`completed` 或安全 `failed` 终态；
- 必要时下一轮恢复候选时间。

SQL 使用同一事务，Memory 使用同一临界区。任何一步失败不得只暴露半份新状态。事件投递采用现有 sequence 语义，不能跳过队首的 Workflow/External Job 完成事件。

## 5. 视频权威状态仓储

### 5.1 状态信封

`WorkflowRecord` 继续作为 Snapshot、列表和路由使用的轻量投影，不承载完整 M11 内部状态。新增 `VideoWorkflowStateEnvelope`，至少包含：

- `workflow_id`、`conversation_id`、`user_id`；
- `schema_version` 与 `state_kind`；
- `workflow_version` 和 `context_version`；
- 规范化 `payload`；
- `payload_sha256`；
- `created_at`、`updated_at`；
- 最近一次成功处理的 Turn/动作幂等身份。

`payload` 保存 M11 恢复所需的权威 DTO，包括 intake、方向、Plan 版本历史、场景包、全局资产图、场景生成状态、合并结果、QA 结果、交付状态和显式人工确认。它不得保存 Authorization、供应商 token、完整供应商请求、原始异常、模型隐藏推理或无界大段 prompt。

读取时必须验证 schema、workflow/conversation/user 归属与 SHA-256。未知版本、摘要不一致、引用其他对话或非法 JSON 一律 fail-closed，不用空状态覆盖坏数据。

### 5.2 Repository 合同

Memory 与 SQL 实现共享同一接口：

- 创建初始状态；
- 按用户、会话和 Workflow 读取；
- 使用 `expected_workflow_version` 做 CAS 更新；
- 在同一提交中更新 `WorkflowRecord`；
- 列出恢复候选但不无界物化；
- 为测试导出稳定、深度只读、可 JSON 序列化的快照。

相同幂等身份和相同摘要重复提交只回读原版本；相同身份但摘要不同必须拒绝。CAS 冲突由执行器重新读取并判断是否已经完成，不能无条件覆盖。

### 5.3 Schema 演进

首版 `schema_version=1`。读取只接受明确注册的版本；将来迁移必须使用独立、可测试的纯函数，并保持旧版本原始数据可审计。本切片不会迁移任何既有对话，因为现有生产对话没有 `supervisor_v1` 视频权威状态。

## 6. VideoLiveWorkflowHandler 动作语义

Handler 接收经过 Validator 的 `WorkflowCommand` 和权威状态，不接收未校验的前端 JSON。九种动作的语义如下：

| 动作 | 视频 Handler 行为 |
| --- | --- |
| `answer_only` | 只生成安全答复，不改变 Workflow 阶段、版本或供应商任务 |
| `start_workflow` | 创建新的视频 Workflow 状态，执行 intake/需求补全；资料不足时打开表单 interrupt，完整后进入三方向生成 |
| `continue_workflow` | 只从当前合法阶段向前推进，例如方向确认、Plan 确认、场景包确认、素材确认、生成/合并/QA/交付 |
| `modify_workflow` | 在当前 Workflow 内应用白名单 patch；Plan 修订、场景包或资产修改必须产生正确的新版本并保持引用一致 |
| `regenerate_stage` | 对允许重生成的当前或指定阶段增加 `stage_version`/attempt，旧产物保留历史但不再作为当前产物 |
| `retry_failed` | 只重试当前失败且合同允许重试的阶段；M06 expired/404 必须创建上层新 attempt，不能重开原 Operation |
| `switch_workflow` | 切换会话内已有且归属正确的视频 Workflow，只改变 active projection，不重复执行阶段 |
| `cancel_workflow` | 将未终态 Workflow 置为 `cancelled`，停止本进程恢复调度；不伪造供应商取消成功，不删除历史产物 |
| `clarify` | 打开可恢复 interrupt，写入固定 reason code 与可展示问题，不改变业务状态 |

每个动作还要满足：

- 只允许操作当前用户、当前对话内的 Workflow、artifact 和 interrupt；
- `requires_confirmation=true` 时必须先打开 interrupt，不能直接执行有副作用的动作；
- `answer_only` 和 `clarify` 不允许携带 patch；
- 同一个 `client_input_id` 重放只返回同一结果；
- 状态不允许的动作返回安全合同错误或定向追问，不能猜测推进。

## 7. 视频阶段与 M11/M06 接线

### 7.1 领域服务

Handler 复用 M11 的确定性 Application Service，不在 Handler 中复制领域规则：

- intake、方向和 Plan 使用 `VideoPlanningWorkflowService`；
- 场景包与全局资产图使用 `VideoScenePackageWorkflowService`；
- 场景素材、场景视频和直接视频生成使用既有 generation 服务；
- 合并、QA、修改循环与最终交付使用既有 postproduction/delivery 服务。

每次服务调用前，从状态信封恢复强类型状态；调用完成后重新序列化并校验状态。不得通过修改私有字段绕过 M11 前置条件。

### 7.2 外部任务

涉及供应商的 start/status 统一经过 M06 `OperationStartCoordinator`、`OperationRecoveryRuntime` 和完成 Outbox：

- Handler 只传入单次调用所需的 Authorization 和规范请求；
- 持久层只保存请求摘要、内部 job、原 provider job ID 和安全状态；
- 重复 start 复用相同 Operation；
- 402 进入 `paused_quota` 并等待用户动作恢复原 provider job；
- timeout/failed 按合同开放 `retry_failed`；
- 404/expired 关闭原 Operation，上层显式创建新 attempt；
- Provider 成功后或 Graph checkpoint 后进程退出，只重放同一完成事件。

测试只使用可控 fake Provider/Clock，不发出真实付费请求。

## 8. 人工确认、消息与前端投影

### 8.1 Interrupt

所有需要人工输入的节点使用真实 LangGraph interrupt，并同步投影到 Runtime Snapshot：

- 需求表单补全；
- 三个创意方向选择或重新生成；
- Plan 审核、修订、历史恢复或新创意重生成；
- 场景包与全局资产确认/编辑；
- 配额暂停恢复；
- 最终视频确认、修改、剪映草稿入口和结束。

Snapshot 不再固定返回 `interrupt=None`，而是返回当前开放 interrupt 的稳定公共投影。`POST /interrupts/{interrupt_id}/responses` 必须校验用户、对话、原 checkpoint 和开放状态；重复相同响应幂等回读，不同响应冲突拒绝。

### 8.2 消息和 Artifact

Handler 不能只修改内部状态。每个用户可感知阶段都要写助手消息或 artifact 引用，并发出 `message.upserted`/`workflow.progressed`：

- 表单、方向、Plan、场景包和 QA 使用稳定结构化 artifact；
- 图片/视频/PPT 等二进制产物只保存自有 HTTPS 引用和安全元数据；
- 新结果不能继承旧结果的下载确认；
- Snapshot 全量恢复和 SSE 增量投影必须得到相同页面结果。

### 8.3 前端动作合同

Supervisor 对话中，原 `legacyArtifactActionsEnabled=false` 继续阻止 v2 按钮推进。需要为 Supervisor 卡片增加统一提交 Client：

- 普通自由文本仍提交消息 Turn；
- 表单提交使用结构化 `start_workflow`/`continue_workflow` patch；
- 确认、修改、重生成、重试、切换和取消使用对应动作；
- 开放 interrupt 时，响应发到 interrupt response API；
- 刷新只读取 Snapshot 与原 job，不自动重发上一次动作；
- 客户端生成稳定 `client_input_id`，网络重试复用该 ID。

前后端共享协议需要加入中文 schema 说明；JSON 无法写注释的键必须在 schema `description` 或同目录中文说明中一一对应。

## 9. Gateway 装配与启用门禁

Gateway 初始化顺序固定为：

1. 创建 Runtime Repository、Graph checkpointer、M06 Operation Repository 和视频状态 Repository；
2. 创建 M11 视频领域服务和 fake/实际 Provider Adapter 装配点；
3. 创建 `VideoLiveWorkflowHandler`；
4. 用真实 Registry 构建 Graph；
5. 创建并启动 `SupervisorTurnExecutor` 与恢复 Runtime；
6. 仅在上述步骤全部成功后，把 `video` 放入本进程计算出的 `primary_execution_intents`；
7. 服务关闭时先停止新领取，再等待/取消本进程任务，最后关闭自有 client。

`primary_execution_intents` 不能只依赖静态配置声明。配置表示“允许启用”，成功注册的 Handler 表示“实际具备能力”，最终集合必须取二者交集。缺少数据库表、checkpointer、Handler、Provider Adapter 或恢复组件时记录不含敏感信息的安全错误，并让视频新对话继续走 `frontend_v2`。

健康检查应暴露安全的注册结果和失败 reason code，不能暴露密钥、完整 URL 查询串或异常字符串。

## 10. 错误、安全与可观测性

- 合同错误、跨租户引用、状态摘要损坏、未知 Provider 状态和 job ID 错配全部 fail-closed。
- 公开错误使用固定 `reason_code`，日志只记录 operation、workflow/turn 的内部安全 ID、异常类型和状态码。
- 指标至少覆盖 Turn 等待/执行/恢复数量、租约冲突、动作分布、interrupt 等待时长、阶段耗时、M06 状态和失败 reason code。
- 指标不得包含用户 prompt、Authorization、token、供应商原始错误或完整资源 URL。
- 上下文预算继续只由共享 `ContextBudgetPolicyProvider` 提供，保持 896K/32K/32K、严格档案和 30 秒退避；本切片不新增节点级窗口常量。
- `deepseek-v4-pro` 档案仍由现有严格验证路径读取；本设计不修改其 1,000,000 tokens 档案。

## 11. 测试与验收

实现阶段必须先写失败测试，再写最小实现。至少覆盖：

### 11.1 Repository 与执行器

- Memory/SQL 状态信封创建、CAS、摘要验证、深度只读和 JSON 稳定序列化一致；
- 同一 Turn 并发只能被一个 worker 领取；租约到期可接管，旧 fencing token 不能提交；
- 同一对话严格按 Turn 顺序执行，不同用户/会话可隔离并行；
- 进程重启可从 accepted/processing/waiting_user 和完成 Outbox 边界恢复；
- 上下文压缩中输入继续排队，退避前不重复执行。

### 11.2 九种动作

- 九种 `AgentAction` 各有成功路径和非法状态拒绝路径；
- `answer_only`/`clarify` 无状态副作用；
- 修改、重生成和重试的版本/attempt 语义正确；
- switch/cancel 不跨对话，不误删产物；
- 明确确认动作与自由文本歧义都经过 Validator。

### 11.3 视频全链路

- 从 0 开始的新视频对话：附件完整保留，表单、三方向、Plan、场景包、素材、场景视频、合并、QA 和交付逐段可推进；
- 任一人工节点刷新后可从 Snapshot 恢复，原 interrupt 可在 Graph 重建后响应；
- 402、timeout、failed、404/expired、部分场景失败和用户修改均走冻结语义；
- Provider start 并发/重启只调用一次；
- 旧 `frontend_v2` 对话和运行中任务行为不变。

### 11.4 Gateway 与前端

- Handler 全部装配成功时，新视频对话冻结为 `supervisor_v1`；
- 任一关键依赖缺失时新视频对话保持 `frontend_v2`；
- Supervisor 表单和按钮只提交 Turn/interrupt，不调用拥有推进权的 legacy v2 API；
- SSE 丢失、页面刷新和客户端重试不重复副作用；
- OpenAPI、TypeScript 类型和 Python 合同一致。

### 11.5 门禁

- 执行定向 pytest、前端测试/类型检查、R2 集成测试和仓库本地全量门禁；
- 执行中文提交信息、人工注释/docstring 和配置说明检查；
- 对比确认 `backend/config.prod.yml` 未变化；
- 不产生真实外部网络调用证据；
- 只有全部门禁绿色后，才允许写本切片完成状态。集成和生产发布仍需后续独立授权。

## 12. 回滚与交付边界

本切片只在隔离分支开发。代码回滚优先撤销本切片提交；运行时回滚由 `primary_execution_intents` 的能力交集自动使新视频对话保持 `frontend_v2`。由于历史对话不迁移，回滚不得把已经冻结为 `supervisor_v1` 的对话暗中改回 v2，而应由原 Handler/恢复 Runtime 完成或显式显示安全不可用状态。

本切片完成后只交付：代码、测试、中文门禁结果和切片状态证据。它不代表 R2 已发布，也不授权修改生产配置、调用付费供应商、执行 M13.3 或合并 Agent→dev。

## 13. 设计验收条件

只有同时满足以下条件，才可认为本设计已实现：

1. Gateway 不再以空 `FakeWorkflowRegistry` 声称接管视频；
2. 一个从 0 新建的 `supervisor_v1` 视频对话能通过九动作合同完整推进并恢复；
3. 完整 M11 状态、M06 Operation、Graph checkpoint、Turn 和事件具有一致的持久化/幂等边界；
4. 前端人工节点不依赖 legacy v2 推进接口；
5. 重启、刷新、并发、配额暂停和失败恢复测试全部通过；
6. 生产配置、历史对话和非视频 intent 没有被改变；
7. 本地中文与代码门禁绿色，且没有真实付费调用。
