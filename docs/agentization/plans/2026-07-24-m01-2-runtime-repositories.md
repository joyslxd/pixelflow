# M01.2 Agent Runtime Repository 双实现实施计划

> **执行约束：** 本计划只覆盖 M01.2。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M01.3。

**目标：** 为 M01.1 的 Workflow、Turn、ContextSummary、Event、Operation 五类业务投影提供同一份异步 Repository Port，以及行为一致的 Memory/SQL 实现；所有读取必须显式携带 `user_id` 并按所有者隔离。

**架构：** Repository 相当于 Java 中一组由同一接口约束的 DAO。调用方只依赖 `AgentRuntimeRepository`；测试环境使用内存实现，真实持久化使用 SQLAlchemy 异步实现。M01.2 只负责创建与读取，不实现更新、conversation revision/CAS、Turn 领取或 Event Outbox claim。

**技术栈：** Python 3.12+、Pydantic v2、SQLAlchemy 2 async、aiosqlite、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不创建切片子分支或切片 worktree。
- 不修改旧 `PixelFlowTaskStore` 行为，不调用任何真实付费 API。
- 每个查询方法都要求非空 `user_id`；跨用户按 ID 查询返回 `None`，列表查询返回空列表。
- 创建时发生主键或唯一约束冲突统一抛出 `AgentRuntimeRecordConflictError`，不得泄露其他用户的记录内容。
- Memory 实现保存和返回隔离副本，调用方修改嵌套 JSON 后不能污染仓库状态。
- M01.3 才实现更新和 CAS；M01.4/M01.5 才实现 Turn/Event 的领取、幂等消费和 cursor/claim。

---

## 任务一：建立 SQL/Memory 共用合同测试

**文件：**

- 新增：`backend/tests/test_agent_runtime_repositories.py`

**接口：**

- 消费：M00 冻结的 `WorkflowRecord`、`TurnRecord`、`ContextSummary`、`AgentEvent`、`ExternalJobStatus`。
- 期望产出：`AgentRuntimeRepository`、`MemoryAgentRuntimeRepository`、`SQLAgentRuntimeRepository`、`OperationRecord`、`AgentRuntimeRecordConflictError`。

- [x] 编写同一套参数化合同，分别创建 Memory Repository 与 SQLite 异步 SQL Repository。
- [x] 对五类记录分别验证创建、按 ID 查询和 conversation 列表查询。
- [x] 验证 Workflow 列表稳定排序、Turn 按数据库 `inbox_sequence` 排序、Summary 按版本排序、Event 按 sequence 排序、Operation 按创建时间和 job ID 排序。
- [x] 验证用户 A 的全部记录对用户 B 都表现为不存在，且跨用户创建相同主键只返回统一冲突。
- [x] 验证 Memory/SQL 返回对象与嵌套 JSON 均为隔离副本。
- [x] 运行新测试，确认因 Repository 尚不存在而在测试收集阶段失败。

## 任务二：实现最小 Repository Port 与内存实现

**文件：**

- 新增：`backend/pixelflow/agent_runtime/persistence/repositories.py`
- 修改：`backend/pixelflow/agent_runtime/persistence/__init__.py`

**接口：**

- `create_workflow/get_workflow/list_workflows`
- `create_turn/get_turn/get_turn_by_client_input_id/list_turns`
- `create_summary/get_summary/list_summaries`
- `create_event/get_event/list_events`
- `create_operation/get_operation/list_operations`

- [x] 定义 `OperationRecord`，覆盖 M01.1 Operation 行中的可恢复任务、请求摘要、阶段版本和 lease 字段，但不包含所有者字段。
- [x] 定义 `AgentRuntimeRepository` Protocol 与统一冲突异常。
- [x] 内存实现使用 `(user_id, record_id)` 保存记录，同时建立全局 ID/唯一键占用检查，模拟 SQL 的唯一约束。
- [x] 所有输入先规范化，所有输出使用深拷贝，避免测试 fake 与 SQL 实现出现可见语义差异。
- [x] 运行合同测试，确认 Memory 参数组变绿，SQL 参数组仍因实现缺失而失败。

## 任务三：实现 SQLAlchemy 异步 Repository

**文件：**

- 修改：`backend/pixelflow/agent_runtime/persistence/repositories.py`

- [x] 使用 M01.1 五张行模型完成只增的 create 方法，提交前 flush；`IntegrityError` 回滚并转换为统一冲突异常。
- [x] 所有 get/list SQL 都同时包含 `user_id` 与业务 ID/conversation 条件，不先查询记录再在 Python 中判断 owner。
- [x] 显式完成行模型与冻结合同的双向映射，包括枚举、UUID、`ActionDecision`、pending external job、JSON 深拷贝和时间字段。
- [x] 按合同规定的稳定顺序返回列表。
- [x] 运行双实现合同，确认全部变绿。

## 任务四：验证、审核与交接

**文件：**

- 修改：`docs/agentization/status/M01-status.md`
- 新增：`docs/agentization/test-reports/M01.2.md`

- [x] 运行 Repository 双实现合同、M01.1 migration、M00 Agent Runtime 合同/配置/旧不变量和旧 Store/对话路由回归。
- [x] 对全部变更 Python 路径运行 ruff，并运行 `git diff --check`。
- [x] 启动独立只读 reviewer，检查所有者隔离是否落在 SQL 条件中、双实现语义是否一致、是否越界实现 M01.3–M01.5。
- [x] 处理 Critical/Important 意见并重新运行切片门禁。
- [x] 用中文更新状态与测试报告，释放写入权，把下一切片设为 M01.3；phase 继续保持 `in_progress`，不得写 ready 状态。
- [x] 通过中文工程规范检查后创建一个中文提交，推送模块分支并核对远端 SHA，随后停止。
