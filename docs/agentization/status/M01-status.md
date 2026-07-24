# M01 持久化、CAS、Turn Inbox 与 Event Outbox

- phase：`ready_for_integration`
- owner：A
- reviewer：`/root/m01_5_independent_review`（M01 权威门禁修复复审）
- base Agent SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- branch：`codex/agent-0.8.4-m01-runtime-store`
- 依赖：M00（已进入 `feature/agent_0.8.4_boguan`）
- 当前切片：`M01.5`
- 最近完成：`M01.5`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- 本切片开始时间：`2026-07-24T17:49:53+08:00`

## 切片

- [x] M01.1 数据模型与 additive migration（2.5h）
- [x] M01.2 SQL/Memory Repository（3h）
- [x] M01.3 revision/CAS/服务端保留 namespace（2.5h）
- [x] M01.4 Turn Inbox 幂等和顺序领取（2h）
- [x] M01.5 Event Outbox/sequence/cursor（2h）

## M01.1 交付与验证

- 新增 Workflow、Turn、ContextSummary、Event Outbox、Operation 五张 SQLAlchemy 表，并注册到共享 metadata 与 PixelFlow 独立 MySQL 初始化清单。
- Alembic `20260724_01` 只新增上述五张表；SQLite 实际 upgrade/downgrade 后旧哨兵表和数据保持不变。
- 结构合同同时校验 ORM 与实际迁移数据库的列、主键、自增、nullable、类型、全部唯一约束和索引；Turn/Event 实际插入验证自增序列为 `1/2`。
- SQLite/PostgreSQL/MySQL 三种方言的 ORM DDL 和 Alembic 离线升级 SQL 均可生成；真实数据库升级/降级本切片只执行 SQLite。
- 定向门禁：`76 passed, 1 warning`；warning 为既有 LangChain pending deprecation。ruff 与 `git diff --check` 通过。
- 独立审核首轮发现 1 个 Important（结构测试覆盖不足），补齐完整结构签名后复审关闭；最终无 Critical、Important 或 Minor。
- 未实现 Repository、CAS、Turn 领取或 Event 投递逻辑，未调用任何真实付费 API。完整记录见 `docs/agentization/test-reports/M01.1.md`。

## M01.2 交付与验证

- 新增统一异步 `AgentRuntimeRepository` Port，以及行为一致的 Memory/SQL 实现，覆盖 Workflow、Turn、ContextSummary、Event 与 Operation 的创建、按 ID 读取和 conversation 列表读取。
- 全部 SQL 读取在数据库谓词中显式携带 `user_id`；跨用户读取表现为不存在，主键与唯一业务键冲突统一转换为不包含记录内容的 `AgentRuntimeRecordConflictError`。
- 双实现共用输入规范化：定长字符串严格对齐 M01.1 数据库列上限；带时区时间统一转为 UTC，naive datetime 明确拒绝；Memory 保存和返回深拷贝。
- 合同测试覆盖五类记录、稳定排序与同时间 tie-break、所有者隔离、全局唯一约束、嵌套 JSON 深拷贝、空白 owner、全部定长列和时间语义。
- M01 范围回归为 `147 passed, 1 warning`；warning 为既有 LangChain pending deprecation。ruff 与 `git diff --check` 通过。
- 独立审核首轮发现 2 个 Important 和 1 组 Minor 覆盖缺口；按 TDD 整改后复审确认 Critical、Important、Minor 均无。
- 本切片未实现 M01.3 的 revision/CAS、M01.4 的 Turn claim 或 M01.5 的 Event Outbox claim，未调用任何真实付费 API。完整记录见 `docs/agentization/test-reports/M01.2.md`。

## M01.3 交付与验证

- 对话聚合新增从 `1` 开始的单调 `revision`；普通更新、Agent Runtime 专用 patch 和剪映原子 patch 只在实际状态变化时递增，no-op 不递增。
- Memory Store 使用对话锁，SQL Store 使用事务内行锁，SQLite 使用 `BEGIN IMMEDIATE`；相同旧 revision 的并发写恰好一个成功，冲突不产生部分更新。
- 所有者谓词先于 revision 比较，跨用户请求继续表现为不存在；HTTP CAS 冲突统一返回 409，未携带 `expected_revision` 的旧前端请求保持兼容。
- `__agent_runtime` 只能由服务端 Repository 方法写入；前端创建和整包 context 更新不能创建、覆盖或删除该命名空间，并继续保留剪映双字段与恢复错误。
- Memory 写入输入和返回快照均使用深拷贝，调用方不能通过修改普通 context、Runtime patch 或剪映嵌套对象绕过 CAS。
- Alembic 旧表迁移使用所有权索引保护 downgrade，预存 revision 列不认领，离线模式 fail-closed；独立 MySQL 初始化仅在缺列时执行兼容升级。
- 新合同为 `10 passed, 1 warning`；M01 范围回归为 `197 passed, 1 warning`。ruff 与 `git diff --check` 通过。
- 独立审核经过两轮 TDD 整改，最终确认 Critical、Important、Minor 均无；未实现 M01.4/M01.5，未调用任何真实付费 API。完整记录见 `docs/agentization/test-reports/M01.3.md`。

## M01.4 交付与验证

- Repository Port 与 Memory/SQL 双实现新增 `enqueue_turn()`：同一 `conversation_id + client_input_id` 的同 owner 重试返回首次持久化快照，不新增 Turn；严格 `create_turn()` 语义保持不变。
- `claim_next_turn()` 按 `inbox_sequence` 领取最早的 `accepted/queued` Turn 并原子更新为 `processing`；同一会话已有处理中 Turn 时阻塞后续领取，终态跳过，不同会话互不阻塞。
- owner 过滤、全局 `turn_id` 与全局幂等键继续 fail-closed；跨 owner 读取仍表现为不存在，不泄露既有 Turn。
- SQLite 同 Engine 使用共享异步锁避免内存 `StaticPool` 嵌套事务，跨 Engine/进程继续由 `BEGIN IMMEDIATE` 数据库写锁保护；生产 SQL 使用 `SELECT ... FOR UPDATE`。
- 新合同为 `12 passed, 1 warning`；M01 精确范围回归为 `209 passed, 1 warning`。ruff、`git diff --check` 与分支策略检查均通过。
- 独立审核首次仅发现 1 个 Minor 测试证据缺口；补齐 Memory/SQL 双实现并发入队合同后复审确认 Critical、Important、Minor 均无。
- 本切片未实现 M01.5 Event Outbox，未调用任何真实付费 API。完整记录见 `docs/agentization/test-reports/M01.4.md`。

## M01.5 交付与验证

- Repository Port 与 Memory/SQL 双实现新增 cursor 增量查询、租约 claim 和投递完成接口；冻结 `AgentEvent`、数据库表与 migration 保持不变。
- `create_event()` 强制 conversation 从 `1` 开始连续递增；跳号、倒序、唯一键或跨 owner conversation 冲突 fail-closed。
- cursor 查询支持从头、续读、末尾、未知和跨 owner 不存在语义；claim 严格阻塞有效租约后的后续事件，过期可接管，完成操作校验当前有效租约并支持幂等重放。
- SQLite 同 Engine 使用共享异步锁，跨 Engine/进程使用 `BEGIN IMMEDIATE`；生产 SQL 使用 `SELECT ... FOR UPDATE`。Memory/SQL 并发 append 与 claim 合同全绿。
- 新合同为 `13 passed, 1 warning`；完整 M01 精确范围门禁为 `222 passed, 1 warning`。ruff、`git diff --check` 与分支策略检查均通过。
- 独立审核确认 Critical、Important、Minor 均为 0；未调用任何真实付费 API。完整记录见 `docs/agentization/test-reports/M01.5.md`。
- 当前无远端 CI，自动化状态保持 `automation_local_ready`；原 M01.5 开发任务只负责准备最终单槽集成本地入口，本次门禁修复任务已获得开发者继续集成的明确授权。

## M01 最终权威门禁修复

- 首次单槽集成因 canonical gate 尚未固化 M01.5 权威清单而安全阻塞，Agent 未更新。
- 已合入最新 Agent 门禁基线，并把 M01 Final 固定为项目 Python 3.12、14 个精确测试文件和限定 Ruff 路径。
- TDD 红灯为 `34 passed, 1 failed`，修复后 Pester 3.4 为 `35 passed, 0 failed`；M01 精确 pytest 为 `222 passed, 1 warning`，Ruff 与 canonical Final 均通过。
- 独立复审 Critical、Important、Minor 均为 0，`Ready to merge: Yes`。完整记录见 `docs/agentization/test-reports/M01-gate-repair.md`。
- 本次开发者已明确授权在状态恢复并 push 后，由同一任务重新创建全新单槽候选；不得复用原阻塞候选。

## 恢复提示

M01 五个切片和最终权威门禁修复均已完成并达到 `ready_for_integration`。当前自动化状态为 `automation_local_ready`；本次开发者已经明确授权当前任务在 push 后继续执行 M01 最终单槽集成。不得自动继续 M02。
- last_integrated_commit：`—`
- locked files：`无`
- checkpoint_status：`ready`
- integration failure evidence：`无`
