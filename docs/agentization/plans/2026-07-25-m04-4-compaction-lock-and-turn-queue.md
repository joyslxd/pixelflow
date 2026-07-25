# M04.4 会话压缩锁与 Turn 队列实施计划

> **执行约束：** 本计划只覆盖 M04.4。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M04.5。

**目标：** 建立可跨进程互斥、可过期接管的 conversation 压缩租约；压缩期间由后端把新输入持久化为 `queued`，压缩成功后原子切回空闲态并把最早输入迁移为 `processing`，失败或暂停时保留恢复标记和全部输入，前端不需要重新发送。

**架构：** SQL 为每个进入 Turn 路径的 conversation 建立永久协调行，状态为 `idle`、`active` 或 `retry_required`，相当于数据库中的分布式锁根；Turn Inbox 相当于按 `inbox_sequence` 排序的消息队列。普通与压缩专用 Repository 入口都先锁同一协调行，消除首次会话无行可锁的竞态。`ConversationCompactionRuntime` 只负责编排“领取租约 → 调用 M04.3 Coordinator → 成功领取下一 Turn / 失败保留队列”；Memory/SQL Repository 负责把租约、入队和状态迁移做成原子操作。M04.5 再在这些稳定状态转换外层补 Outbox 事件与 `SummaryVerifier`。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy async、Alembic、pytest、ruff。

## 方案选择

1. 只用进程内 `asyncio.Lock`：实现简单，但多个 worker 会同时压缩同一对话，进程重启后也没有可恢复证据，不满足冻结设计的多进程锁要求，因此不采用。
2. 在整个 LLM 压缩期间持有数据库行事务锁：可以跨进程互斥，但会长期占用连接和事务，并阻止压缩期间输入落库，因此不采用。
3. 使用永久 conversation 协调行和短事务维护带过期时间、随机 fencing token 的状态机；输入入队、普通领取和租约检查都锁定该行，压缩过程不持有数据库连接，成功/失败再用 token 做原子收尾。该方案同时支持首次会话稳定互斥、持续入队、过期接管和陈旧 worker fail-closed，因此采用。

## 全局约束

- conversation 锁必须跨 Repository 实例生效；Memory 与 SQL 双实现遵循同一合同，SQL 通过租约表和短事务保证多进程互斥。
- 租约使用随机 token 防止过期 worker 在新 owner 接管后误释放新锁；到期时间必须为带时区 UTC 时间且晚于领取时间。
- 领取租约时不得存在 `processing` Turn；既有 `accepted` Turn 原子迁移为 `queued`。
- `active` 或 `retry_required` 期间，新输入通过普通或专用入口都只能保存为 `queued`；同一 `conversation_id + client_input_id` 重试返回原 Turn，不新增记录，所有领取入口都 fail-closed。
- 压缩成功时，在同一事务中校验 token、把协调行切回 `idle` 并清空租约字段，再按 `inbox_sequence` 把最早 `accepted/queued` Turn 更新为 `processing`。
- 压缩异常或 `paused` 时，把当前有效协调行切为 `retry_required`，不领取 Turn；所有输入继续持久化，后续 worker 用新 token 从数据库队列接管。
- 原始消息、Plan、创作合同、场景蓝图、资产清单、pending action、operation 和已保存摘要均不删除、不改写。
- 本切片不实现 M04.5 的 started/progress/completed/failed Outbox 事件和 `SummaryVerifier`，不调用真实 LLM 或任何付费供应商。
- 不修改两个长期 feature 分支、`status/BOARD.md`、运行配置或 content-app 合同。

---

### 任务一：用失败测试冻结租约与压缩期入队

**文件：**

- 新增：`backend/tests/test_agent_runtime_compaction_queue.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/models.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/__init__.py`
- 新增：`backend/packages/harness/deerflow/persistence/migrations/versions/20260725_03_compaction_locks.py`
- 修改：`backend/tests/test_agent_runtime_migration.py`

- [x] 为 Memory/SQL 双实现编写同一对话并发领取只有一个成功、不同对话互不阻塞、过期 token 可接管且陈旧 token 不能收尾的失败测试。
- [x] 编写活跃租约期间连续提交 3 条输入的失败测试，断言全部为 `queued`、顺序稳定、同一 client input 重试不重复。
- [x] 编写 owner 隔离、非法时间和已有 `processing` Turn 时拒绝压缩的 fail-closed 用例。
- [x] 运行新测试，确认因租约合同和 Repository 方法不存在而失败。
- [x] 最小实现永久协调行、additive migration、Memory/SQL 短事务和普通/专用入队入口。

### 任务二：用失败测试冻结成功迁移与失败恢复

**文件：**

- 修改：`backend/tests/test_agent_runtime_compaction_queue.py`
- 修改：`backend/pixelflow/agent_runtime/context/compaction.py`
- 修改：`backend/pixelflow/agent_runtime/context/__init__.py`

- [x] 编写阻塞 fake Coordinator 的并发测试：压缩执行期间输入可继续入队，成功后只把最早 Turn 更新为 `processing`。
- [x] 编写 Coordinator 异常与 `paused` 用例：保留 `retry_required` 恢复标记、输入仍为 `queued`、随后重新压缩可从原队列继续，不需重新提交 `/start`。
- [x] 编写陈旧 worker 收尾失败测试，断言不能领取队列或改写新 owner 的租约。
- [x] 实现 `ConversationCompactionRuntime` 及严格结果 DTO，仅组合 M04.3 Coordinator 和 Repository，不复制阈值或摘要逻辑。

### 任务三：回归、审核与交接

**文件：**

- 修改：`docs/agentization/status/M04-status.md`
- 新增：`docs/agentization/test-reports/M04.4.md`

- [x] 运行 M04.4 新测试、migration/Turn Inbox/Repository、M04.1–M04.3、M03 相邻和 DeerFlow summarization/harness 回归。
- [x] 对全部变更 Python 路径运行 `ruff check`、`ruff format --check`，并运行 `git diff --check`。
- [x] 启动独立只读 reviewer，检查跨进程互斥、fencing、原子状态迁移、失败恢复、owner 隔离、输入不重发和 M04.5 越界。
- [x] 处理全部 Critical/Important 意见，重新运行本切片完整验证。
- [x] 用中文更新状态和测试报告，勾选 M04.4、把下一切片设为 M04.5，释放当前写入者；phase 保持 `in_progress`。
- [x] 运行中文工程规范检查，创建一个中文独立 commit，推送 `codex/agent-0.8.4-m04-context-compaction` 并核对远端 SHA后停止。
