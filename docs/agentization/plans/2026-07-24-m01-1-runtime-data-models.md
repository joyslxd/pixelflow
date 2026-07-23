# M01.1 Agent Runtime 数据模型与迁移实施计划

> **执行约束：** 本计划只覆盖 M01.1。完成测试、独立审核、状态记录、独立提交和推送后必须停止，不得进入 M01.2。

**目标：** 为 `WorkflowRecord`、`TurnRecord`、`ContextSummary`、`AgentEvent` 和外部 `Operation` 建立 SQLAlchemy 行模型及可逆 additive migration，不改变旧 Store 行为。

**架构位置：** 五张新表相当于 Java 项目中独立的 Agent Runtime 持久化 Entity。它们复用 DeerFlow 的 SQLAlchemy `Base` 和 Alembic 环境，同时加入 PixelFlow 独立 MySQL 初始化表清单。M01.1 只定义表结构；SQL/Memory Repository、CAS、Turn Inbox 领取和 Event Outbox 投递分别留给 M01.2–M01.5。

**技术栈：** Python 3.12+、SQLAlchemy 2、Alembic、SQLite/aiosqlite、pytest、ruff。

---

## 任务一：先建立迁移与模型失败测试

**文件：**

- 新增：`backend/tests/test_agent_runtime_migration.py`

1. 声明五张新表及冻结字段、唯一约束和关键索引的期望结构。
2. 验证 ORM metadata 注册五张表，并验证独立 MySQL 初始化清单包含同一组表。
3. 在包含旧哨兵表和旧数据的 SQLite 数据库上执行 Alembic `upgrade head`，验证只新增 Agent Runtime 表。
4. 执行 `downgrade base`，验证只移除新表，旧表和旧数据仍保留。
5. 运行该测试并确认因持久化模型/迁移尚不存在而失败。

## 任务二：实现五张 ORM 表

**文件：**

- 新增：`backend/pixelflow/agent_runtime/persistence/__init__.py`
- 新增：`backend/pixelflow/agent_runtime/persistence/models.py`
- 修改：`backend/packages/harness/deerflow/persistence/models/__init__.py`
- 修改：`backend/pixelflow/tasks/mysql.py`

1. 建立 Workflow 表，保存合同快照、当前阶段、版本、Artifact 引用和上下文版本。
2. 建立 Turn Inbox 表，以数据库自增顺序固定接收顺序，并约束 `conversation_id + client_input_id` 幂等。
3. 建立结构化 Summary 表，保存版本、内容 hash、关键事实和证据范围。
4. 建立 Event Outbox 表，约束 conversation 内 sequence/cursor 唯一，并预留投递状态与 lease 字段。
5. 建立 Operation 表，保存 request hash、幂等键、阶段版本、供应商任务引用和轮询 lease。
6. 将五张表注册到共享 metadata 和 PixelFlow 独立 MySQL 初始化清单。

## 任务三：实现可逆 additive migration

**文件：**

- 新增：`backend/packages/harness/deerflow/persistence/migrations/versions/20260724_01_agent_runtime_tables.py`

1. `upgrade()` 只创建五张新表、约束和索引，不修改任何旧表。
2. `downgrade()` 按依赖逆序只删除本迁移创建的对象。
3. 使用 SQLite 实际执行升级/降级结构测试，保证迁移不是仅靠 ORM `create_all()` 通过。

## 任务四：验证、审核与交接

**文件：**

- 修改：`docs/agentization/status/M01-status.md`
- 新增：`docs/agentization/test-reports/M01.1.md`

1. 运行新迁移测试、M00 Agent Runtime 合同测试、旧 Store/对话路由回归和 ruff。
2. 运行 `git diff --check` 与中文工程规范检查。
3. 启动独立只读 reviewer，检查合同对应关系、可逆性、旧表安全、跨数据库兼容和越权修改。
4. 处理有效意见后重新运行全部切片门禁。
5. 用中文更新状态和测试报告，将当前切片标记完成、下一切片指向 M01.2；phase 保持 `in_progress`，不得写任何 ready 状态。
6. 创建一个中文独立 commit，推送 `codex/agent-0.8.4-m01-runtime-store`，复核远端 SHA 后停止。
