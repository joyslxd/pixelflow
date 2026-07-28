# M06.4 Operation 完成事件与 Workflow 恢复实施计划

> **执行要求：** 使用 `superpowers:test-driven-development` 严格执行红灯、绿灯、重构。本计划只覆盖 M06.4；完成独立只读审核、一个中文提交和 push 后必须停止。

**目标：** 把 Provider 终态与 `external_job.state_changed` 完成事件原子写入同一个持久化 Repository，并通过带租约的定向完成事件 claim 恢复原 Workflow Graph，覆盖“Provider 已成功、Graph checkpoint 尚未写入时进程崩溃”的窗口，同时保证重复终态、并发恢复和租约过期重放不会重新启动供应商任务。

**架构：** `OperationCompletionCoordinator` 类似 Java 的事务型 Application Service：它只接收 M06.3 已安全归一的 `ProviderJobSnapshot`，把成功、业务失败和超时映射到冻结的 Operation 终态，再委托 Repository 在单个内存临界区或 SQL 事务中同时更新 Operation、清除轮询租约并追加稳定 ID 的 Outbox 事件。`OperationCompletionDispatcher` 类似幂等消息消费者：按完成事件 ID 领取独立投递租约，把同一事件及其 ID 交给 `WorkflowGraphResumePort`，成功后确认投递；进程在 Graph 恢复前崩溃时由新 worker 继续领取，进程在 Graph 已持久化 checkpoint、确认 Outbox 前崩溃时则依靠稳定事件 ID 让 Workflow 端幂等去重。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy async、SQLite、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不建立切片子分支或额外 worktree。
- 不新增数据库表、字段、索引、migration、配置、HTTP API 或 content-app 合同。
- 不调用真实图片、视频、PPT、视频分析、剪映、LLM 或其他付费 API；测试只使用 Memory/SQLite Repository 和确定性 fake Graph Port。
- 不实现 M06.5 的 lifespan 扫描、shutdown、restart worker 装配、Provider 404/expired 或人工恢复入口。
- `polling` 与 `paused_quota` 不是本片终态完成事件；额度暂停的重启/人工继续语义留给 M06.5。M06.4 只接受 `succeeded/failed/timeout`。
- Operation 终态与完成事件必须原子提交；任何冲突都不得留下“终态无事件”或“事件存在但 Operation 未终态”的半状态。
- 完成事件 ID、cursor 和 run ID 必须只由内部 job ID 派生且稳定；重复观察同一终态只能回读同一事件，不追加第二条 sequence。
- Workflow Graph 恢复是至少一次投递，事件 ID 是强制幂等键。恢复 Port 不得调用供应商 start；恢复失败或进程崩溃只允许在租约过期后重放同一完成事件。
- Authorization、token、API key、secret、凭据、供应商原始错误、完整 traceback 和请求体不得进入 Operation、事件、状态或测试快照。
- 所有新增或修改的人工注释、计划、测试报告和 commit 使用中文主体语义。

---

### 任务一：冻结事务性终态与 crash window 合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_operation_completion.py`

- [x] **步骤 1：编写 Memory/SQL 原子终态合同**

为已领取的 `polling` Operation 记录安全成功快照，断言 Repository 在同一原子操作中写入 `succeeded`、清空轮询租约/时间，并追加唯一 `external_job.state_changed` 事件；事件包含稳定 job/workflow/stage/version/attempt/status/reason/message/result，不包含请求摘要、幂等键或凭据。

- [x] **步骤 2：编写重复终态与冲突合同**

重复提交完全相同终态必须回读同一 Operation 和同一事件；不同终态、provider job ID 错配、未持有/已过期租约、其他 owner/conversation、`polling` 或 `paused_quota` 快照必须 fail-closed，且不得产生第二事件或半状态。

- [x] **步骤 3：编写并发 sequence 与双 worker 合同**

Memory 与两个独立 SQLite Engine 同时提交同一终态时，只能得到一个完成事件；完成事件与同会话其他 Outbox 事件并发时 sequence 仍连续，Repository 冲突可安全重试。

- [x] **步骤 4：编写 Graph 恢复与终态 claim 合同**

只有一个 worker 能领取指定完成事件；Graph Port 获得原 workflow namespace、稳定完成事件 ID 和安全 payload。恢复成功后确认投递；恢复异常不确认，租约到期后新 worker 获得同一事件 ID。

- [x] **步骤 5：编写两段 crash window 合同**

模拟 Provider 成功并完成事务后、Graph 调用前崩溃：新 Dispatcher 只恢复持久化事件，不调用 Provider start。模拟 Graph 已按事件 ID 保存 checkpoint、Outbox 确认前崩溃：重放仍携带同一幂等键，由 fake Graph 去重，业务恢复只生效一次。

- [x] **步骤 6：运行新测试并确认 RED**

```powershell
& E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe `
  -m pytest tests/test_agent_runtime_operation_completion.py -q
```

预期因 `OperationCompletionCoordinator`、`OperationCompletionDispatcher` 与 Repository 原子终态方法不存在产生明确收集失败。

### 任务二：实现最小事务性完成与恢复投递

**文件：**

- 新增：`backend/pixelflow/agent_runtime/jobs/completion.py`
- 修改：`backend/pixelflow/agent_runtime/jobs/__init__.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`

- [x] **步骤 1：定义稳定完成记录与 Graph Resume Port**

定义深拷贝、只读的 Operation/事件完成结果，以及只暴露 `resume_external_job(namespace, completion_event, idempotency_key)` 的异步 Port。Port 文档明确 `idempotency_key == event_id`，调用方和实现方不得重新启动 Provider。

- [x] **步骤 2：实现完成事件身份与安全 payload**

从内部 job ID 派生固定长度事件 ID、cursor 和 run ID；只序列化 M06.3 Snapshot 的安全业务结果和必要 Operation 身份。提交前再次校验 provider job ID、终态映射、owner/conversation 与有效轮询租约。

- [x] **步骤 3：实现 Memory/SQL 原子终态方法**

Memory 同时持有 Operation/Event 写锁；SQL 使用同一个写事务锁定 Operation 和当前最后事件行。Operation 终态更新、租约清理和事件插入必须一起成功或一起回滚；重复完全相同终态返回原记录，不同终态或事件不一致抛出安全冲突。

- [x] **步骤 4：实现定向完成事件 claim 与确认**

Repository 按 owner、conversation、event ID、类型和 job ID 领取投递租约，避免被同会话其他事件阻塞；已确认事件不再领取，过期租约允许接管。确认仍校验 owner、worker 和未过期租约。

- [x] **步骤 5：实现 Coordinator 与 Dispatcher**

Coordinator 对可重试 sequence 冲突做有限重读/重试；Dispatcher 领取事件、构造原 workflow namespace、调用 Graph Port，再确认投递。Graph 调用异常原样向上抛出但不得泄漏事件安全边界之外的信息，也不得提前确认。

- [x] **步骤 6：运行定向 GREEN**

只实现让本片失败测试通过的最小行为，不加入 M06.5 扫描器、生命周期任务或 404/expired 策略。

### 任务三：回归、独立审核与交接

**文件：**

- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`docs/pixelflow-agent-skill-flow-latest-design.md`
- 修改：`docs/agentization/status/M06-status.md`
- 新增：`docs/agentization/test-reports/M06.4.md`
- 修改：`docs/agentization/plans/2026-07-28-m06-4-operation-completion-resume.md`

- [x] **步骤 1：运行 M06.4 范围回归与静态检查**

覆盖 completion、Provider Adapter、operation、lease、Event Outbox、M00 合同、M01 Repository/migration、M02 graph namespace/interrupt/recovery、全部 Agent Runtime 扩展回归、Ruff、格式和 `git diff --check`。

- [x] **步骤 2：发起独立只读审核**

审核重点为终态/Event 原子性、稳定事件身份、sequence 竞争、provider job ID 绑定、终态租约、重复终态、Graph 至少一次投递与 checkpoint 去重、owner 隔离、凭据泄漏和 M06.5 越界。

- [x] **步骤 3：处理 Critical/Important 并重新验证**

每个有效问题先补失败合同再做最小修复；最终记录 Critical、Important、Minor 状态。

- [x] **步骤 4：完成中文状态与测试记录**

勾选 M06.4，释放唯一写入权，下一切片设为 M06.5；phase 保持 `in_progress`。M06.4 不是 `phased-rollout-plan.md` 明确检查点或最后切片，不更新 `status/BOARD.md`，不写任何 integration ready 状态，也不提示执行 9.10A。

- [x] **步骤 5：执行中文工程门禁并提交推送**

只暂存本切片文件，使用一个中文 commit；push `codex/agent-0.8.4-m06-external-jobs` 后核对远端 SHA 并停止。
