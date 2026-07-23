# M01 持久化、CAS、Turn Inbox 与 Event Outbox

- phase：`in_progress`
- owner：A
- reviewer：`/root/m01_2_independent_review`
- base Agent SHA：`5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- branch：`codex/agent-0.8.4-m01-runtime-store`
- 依赖：M00（已进入 `feature/agent_0.8.4_boguan`）
- 当前切片：`M01.3`
- 最近完成：`M01.2`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：无
- 本切片开始时间：无

## 切片

- [x] M01.1 数据模型与 additive migration（2.5h）
- [x] M01.2 SQL/Memory Repository（3h）
- [ ] M01.3 revision/CAS/服务端保留 namespace（2.5h）
- [ ] M01.4 Turn Inbox 幂等和顺序领取（2h）
- [ ] M01.5 Event Outbox/sequence/cursor（2h）

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

## 恢复提示

下一次只执行 M01.3：先从远端恢复同一模块分支/worktree，复核 M01.2 双实现合同，再以失败测试实现 conversation revision/CAS 与服务端保留 namespace；不得提前实现 M01.4 的 Turn Inbox claim 或 M01.5 的 Event Outbox claim。
