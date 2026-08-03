# R2 视频 status 402 持久化暂停与恢复设计

## 1. 背景与结论

Task 14 真实全流程门禁证明，当前 M06 在 Provider status 返回 402 后只把原 Operation 保持为
`polling` 并清空 `next_poll_at`。生产 Graph 不会收到额度暂停通知，原 Turn 不会打开可响应的
interrupt；唯一的 `OperationRecoveryRuntime.recover_manually()` 也没有生产调用者。因此，测试中
先手工恢复再调用 `VideoLiveOperationBridge.start()` 的做法不能证明真实用户可以携新凭据恢复原
provider job。

本设计采用持久化 quota 状态 Outbox。402 暂停和用户恢复分别写入非终态
`external_job.quota_state_changed` 事件，由专用 Dispatcher 以稳定 Graph checkpoint 驱动原
Workflow 与原 Turn。用户交互复用现有 `retry_failed`，不新增第十种 `AgentAction`。

同一个 provider job 恢复后再次出现 402 时，允许生成新的暂停 revision 和新的 interrupt；旧
revision 的响应必须失败关闭。整个过程不重新调用 Provider start，不迁移对话归属，也不把凭据写入
任何持久化或公开投影。

## 2. 范围与非目标

### 2.1 本切片范围

- 为 Memory 与 SQLite Operation 增加单调递增的 `quota_pause_revision`。
- 原子提交“暂停 Operation + pause Outbox”和“恢复 Operation + resume Outbox”。
- 增加 quota 状态事件的领取、租约、投递、确认和重启恢复。
- 在原 Graph/Workflow/Turn 上投影 `paused_quota`、授权 interrupt 和恢复后的 `running`。
- 复用 `retry_failed`，并通过现有 Router、Service、Executor 与瞬时 Credential Vault 接收新
  Authorization。
- 补齐真实公共入口、Memory/SQLite、崩溃窗口、重复 402、凭据安全和现有失败矩阵测试。

### 2.2 明确不在本切片范围

- 不修改 `backend/config.prod.yml`，不把生产从 R1 切到 R2。
- 不调用真实付费 Provider，不新增真实供应商测试。
- 不新增前端动作或新的 HTTP API；前端继续使用现有 `retry_failed` 和 interrupt response。
- 不把 402 伪造成终态 completion，不改变 timeout、failed、404/expired 的冻结语义。
- 不迁移历史对话和运行中任务，不执行 M13.3、R2 生产发布或 Agent→dev 合并。

## 3. 环境与生产配置边界

测试、开发和生产使用同一套 Python 生产代码，不维护“测试实现”和“生产实现”两份逻辑。测试只在
外部 Client 边界注入 Fake Provider、可控 Clock 和临时数据库。

本切片会提交 Operation/SQL 模型和数据库迁移源码，并只在本地临时数据库执行迁移验证。本次不把
迁移应用到生产服务器。生产 `backend/config.prod.yml` 继续保持已经发布的 R1：
`assist / enabled_intents=[] / 100% / context_compaction=true`。因此即使代码候选包含 R2 能力，
生产也不会接管新视频对话。未来应用数据库迁移和切换 `primary(video)` 必须分别获得独立发布授权。

## 4. 数据合同

### 4.1 Operation

`OperationRecord` 和 SQL Operation 行增加：

```text
quota_pause_revision: int >= 0，默认 0
```

字段只表示同一内部 job 经历过多少次有效 402 暂停，不表示 Provider attempt。402 不改变
`workflow_id + stage + stage_version + attempt`，也不改变 `provider_job_id`。

### 4.2 quota 状态事件

新增非终态事件 `external_job.quota_state_changed`，安全 payload 固定包含：

- `job_id`、`workflow_id`、`stage`、`stage_version`、`attempt`；
- `quota_pause_revision`；
- `quota_state`，只允许 `paused` 或 `resumed`；
- 固定安全 `reason_code`；
- 不含 Authorization、Provider 请求、原始响应、异常字符串或完整资源 URL。

事件 ID 由 `job_id + quota_pause_revision + quota_state` 规范哈希派生。同一状态重试只能回读同一
事件；第二次 402 使用 revision 2，因此得到新的 pause/resume 事件。

### 4.3 Workflow 与 interrupt

pause 事件把权威 Workflow 投影为 `paused_quota`，保留原 pending Operation，并在原 Turn 打开
唯一 `authorization_required` interrupt。interrupt 的安全恢复动作固定为：

```text
action = retry_failed
patch = {job_id, quota_pause_revision}
```

公开 patch 不含 provider job ID 和凭据。Handler 按 user、conversation、workflow、job、attempt 和
revision 重新读取并校验权威 Operation，不能信任前端自行提交的身份字段。

## 5. 402 暂停事务

Provider status 返回 `paused_quota` 时，Recovery Runtime 不创建 completion。Repository 在同一
Memory 临界区或 SQL 事务中：

1. 校验当前 worker 的有效 poll lease；
2. 校验 Operation 仍为 `polling` 且绑定原 provider job；
3. 清空 `next_poll_at` 和 poll lease；
4. `quota_pause_revision + 1`；
5. 写入该 revision 的 `quota_state=paused` Outbox；
6. 返回深度只读、可稳定 JSON 序列化的 Operation 与事件快照。

事务提交前退出时，租约到期后新 worker 可以重新查询 Provider；事务提交后退出时，暂停事件留在
Outbox，由新进程继续投递，不会永久丢失通知。

## 6. pause 事件投递

专用 quota Dispatcher 复用完成事件的领取租约、稳定事件 ID 和 Graph checkpoint 规则：

1. 按事件 ID 领取 pause event；
2. 创建或复用 `quota-paused:<event_id>` Graph checkpoint；
3. 校验 Operation、Workflow、原 Turn 和 revision 身份；
4. 将 Workflow 更新为 `paused_quota`，在原 Turn 打开唯一授权 interrupt，并写安全消息/事件；
5. 原子提交 Workflow、Turn、interrupt 与公开投影；
6. 用实际完成时间确认事件租约。

Graph checkpoint 后退出、Repository 提交后退出或确认前退出都只重放同一事件和 interrupt。旧 worker
租约过期后不得确认事件。

## 7. 用户恢复事务

用户响应授权 interrupt 时，继续走现有公开链路：

```text
Router -> AgentRuntimeService -> SupervisorTurnExecutor
       -> Graph -> VideoLiveWorkflowHandler
       -> VideoLiveOperationBridge
```

Router 从本次 Authorization 创建 `TransientTurnCredential`。Handler 只接受
`paused_quota + retry_failed`，并按安全 patch 读取权威 Operation。缺少凭据时不恢复 Operation，而是
在同一 pause revision 上打开新的授权 interrupt。

凭据存在时，Bridge 在一次调用栈内验证其可用性，随后 Repository 在同一事务中：

1. 校验 user、conversation、workflow、job、attempt 和 expected revision；
2. 校验 Operation 仍为暂停轮询，且没有该 revision 的 resume event；
3. 恢复原 Operation 的 `next_poll_at`，不调用 Provider start；
4. 写入同 revision 的 `quota_state=resumed` Outbox；
5. 返回同一 job/attempt/provider job 的稳定快照。

Executor 继续在 `finally` 中销毁凭据。事务只保存“已经由当前认证请求批准恢复”的安全动作，不保存
Authorization 或其摘要。

## 8. resume 事件投递与轮询顺序

resume event 使用 `quota-resumed:<event_id>` checkpoint，把 Workflow 从 `paused_quota` 恢复为
`running`，关闭对应 interrupt，并确认 Outbox。

Recovery Runtime 每轮顺序固定为：

1. 投递未确认的 quota pause/resume 事件；
2. 投递未确认的终态 completion 事件；
3. 领取到期的 Provider 轮询任务。

Repository 的 due-operation 查询还必须排除存在未确认 quota event 的 Operation。这样即使恢复事务已
提交、Graph 尚未更新，Provider status 也不会越过 resume event。resume event 确认后才允许查询原
provider job。

如果 resume 事务提交后进程退出，Outbox 会完成 Workflow 恢复，不再需要保存凭据。如果用户响应已经
登记但 resume 事务尚未提交就退出，Operation 保持暂停；恢复执行器拿不到已销毁凭据时会重新打开新的
授权 interrupt。

## 9. 重复、并发与第二次 402

- 相同 interrupt response 和 `client_input_id` 重放只回读同一 Turn，不重复恢复。
- 同 revision 的重复 resume 请求只回读同一 resume event。
- 两个并发用户请求只能有一个通过 Repository CAS；失败方读取已提交结果。
- revision 已过期、job/attempt 不匹配、跨用户或跨会话引用固定失败关闭。
- 恢复后再次出现 402 时，Repository 递增 revision，生成新的 pause event 与 interrupt。
- revision 1 的旧响应不能恢复 revision 2；Provider start 总计仍不增加。

## 10. 错误与安全边界

- 402 不是终态，不生成 Operation completion event。
- timeout/failed/404/expired 继续走现有 completion handler 和冻结 attempt 语义。
- 公开失败使用固定 reason code，不返回 Provider 原始错误。
- Operation、quota Outbox、Turn、checkpoint、Snapshot/SSE、完成投影和日志都不得出现凭据 marker。
- `serialize_as_any=True` 的消费边界必须立即重建严格 DTO；对抗性子类的额外字段必须失败关闭或被严格
  DTO 丢弃，不能进入持久化与公开投影。
- context 预算继续保持 896K/32K/32K、严格已验证模型档案和 30 秒退避；本设计不修改这些配置。

## 11. 组件职责

| 组件 | Java 类比 | 变更职责 |
| --- | --- | --- |
| Operation Repository | Repository | pause/resume 与 quota Outbox 原子事务、revision CAS、Memory/SQL 同构 |
| OperationRecoveryRuntime | 定时任务 Service | 402 调用 pause 事务；先投递 quota 状态事件，再轮询 |
| QuotaStateDispatcher | Outbox Consumer | 事件租约、Graph checkpoint、原 Turn/interrupt 投影与确认 |
| VideoLiveOperationBridge | 防腐 Client/Application Service | 用瞬时凭据授权恢复原 Operation，不调用 Provider start |
| VideoLiveWorkflowHandler | 领域 Application Service 门面 | 处理 paused_quota/retry_failed，校验权威身份 |
| Gateway Factory | Spring Configuration 类比 | 注入 Dispatcher、Bridge、Graph 与 Recovery Runtime；依赖缺失时保持 v2 |

前端不新增组件或动作；只消费现有授权 interrupt 并提交 `retry_failed`。

## 12. 测试与验收

实现必须严格 RED→GREEN，至少覆盖：

### 12.1 Repository 与 Outbox

- Memory/SQLite pause 事务同构，revision 从 0 到 1；
- resume 事务保持原 job、attempt 和 provider job，Provider start 新增 0；
- pause/resume 重复提交回读同一事件；
- 并发 CAS、事件租约、租约过期接管和实际完成时间确认；
- due-operation 不越过未确认 quota event；
- 第二次 402 得到 revision 2，旧 revision 恢复失败。

### 12.2 Graph 与崩溃恢复

- pause/resume 各覆盖事件领取前后、Graph checkpoint 前后、Repository 提交前后和 Outbox 确认前退出；
- 重启后恢复同一事件、原 Turn、原 Workflow 和唯一 interrupt；
- 重复 worker 不重复消息、中断或状态事件；
- resume event 确认前不执行下一次 Provider status。

### 12.3 公共全流程

- 真实 FastAPI action 启动 fake Operation，生产 worker 查询得到 402；
- Snapshot/SSE 同源显示 `paused_quota` 与授权 interrupt；
- 用户以新的 Authorization 提交 `retry_failed`；
- 新凭据只消费一次并由生产调用链销毁；
- 同一 provider job/attempt 恢复，Provider start 仍为 1；
- 后续 status 成功并沿原 Graph 继续；
- 同一 job 再次 402 后 revision 2 可恢复，revision 1 响应失败关闭。

### 12.4 故障与泄漏矩阵

- timeout、failed、404/expired、partial failure 真实经过生产 CompletionHandler 与 Graph，不能预置
  Turn 后原样比较；
- 每个 fault 使用精确 reason、Turn/interrupt、attempt 和 provider job 断言；
- 对 402、timeout、failed、404 的安全日志逐项扫描；
- 对抗 Turn、Context 和 completion 子类实际穿过 Executor/Handler；
- 全部持久化、checkpoint、Snapshot/SSE、投影和日志无凭据 marker。

### 12.5 最终门禁

- Task 14 两文件与 Graph/Executor/Operation 聚焦测试；
- 后端 Runtime/视频组合与后端全量测试；
- Web test、lint、build-prod；
- Ruff、中文工程门禁、`git diff --check`、占位符扫描；
- `backend/config.prod.yml` 相对长期分支零差异；
- 未调用真实付费供应商，测试进程无残留。

## 13. 回滚与发布边界

代码回滚使用本设计后续实现提交的反向提交。数据库迁移必须设计为在 R2 尚未启用、没有 quota revision
数据时可安全回退；一旦未来生产产生 revision 数据，回退必须先关闭 R2 并保留列，不得直接丢弃审计
数据。

本设计和后续实现只属于 R2 开发候选。完成后状态仍为“开发切片已完成，待独立单槽集成”。不修改生产
R1 配置，不部署、不调用真实付费 Provider、不执行 M13.3 或 Agent→dev 合并。

## 14. 设计验收条件

只有同时满足以下条件，才能关闭 Task 14 的 402 门禁：

1. 402 pause 与用户 resume 都有持久化、可重放的非终态 Outbox；
2. 原 provider job、attempt 和 start 次数保持不变；
3. 每次 402 有独立 revision、事件和 interrupt，旧响应失败关闭；
4. 新 Authorization 只由当前请求消费并销毁，所有存储和投影无泄漏；
5. 任一崩溃窗口都能恢复原 Graph、Workflow、Turn 与 Operation；
6. timeout/failed/404/partial 的 Task 14 证据真正经过生产完成链路；
7. 全部门禁通过且生产配置保持 R1。
