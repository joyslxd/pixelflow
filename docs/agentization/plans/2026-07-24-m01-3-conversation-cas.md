# M01.3 对话 revision、CAS 与服务端保留命名空间实施计划

> **执行约束：** 本计划只覆盖 M01.3。完成 TDD、测试、独立审核、状态记录、一个独立提交和推送后必须停止，不得进入 M01.4。

**目标：** 为旧对话聚合增加单调递增的 `revision`，用行锁与比较后更新保护服务端 Agent Runtime 状态，并确保前端提交的整包 context 永远不能覆盖 `__agent_runtime` 或剪映草稿状态。

**架构：** `PixelFlowConversationRecord` 相当于带 `@Version` 字段的聚合根 DTO；Memory Store 用 conversation 锁模拟原子比较，SQL Store 在数据库事务和行锁内完成比较。旧前端 `PUT` 保持兼容，但只能写普通 context；新 Runtime 必须通过内部专用 Repository 方法携带 `expected_revision` 修改 `__agent_runtime`，冲突时 fail-closed。

**技术栈：** Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、SQLite/aiosqlite、pytest、ruff。

## 全局约束

- 不修改两个长期 feature 分支，不创建切片子分支或额外 worktree。
- 不修改前端，不调用任何真实付费 API。
- `revision` 初始值为 `1`，每次成功修改对话普通字段、Agent Runtime 保留区或剪映状态后递增一次。
- `expected_revision` 不匹配时抛出统一冲突，不执行部分写入；跨用户访问仍表现为不存在。
- 旧前端未携带 `expected_revision` 时保持兼容，但它提交的 `context.__agent_runtime` 必须被忽略。
- `__agent_runtime` 只能通过 Store 的服务端专用方法修改；普通 context 全量替换继续保留现有剪映双字段和恢复错误。
- 不实现 M01.4 的 Turn Inbox claim，也不实现 M01.5 的 Event Outbox claim/cursor。

---

## 任务一：建立 revision、CAS 与命名空间失败合同

**文件：**

- 新增：`backend/tests/test_agent_runtime_conversation_cas.py`

**接口：**

- 期望产出：`ConversationRevisionConflictError`。
- 期望产出：`patch_agent_runtime_conversation_context(conversation_id, user_id, expected_revision, runtime_patch)`。
- 期望产出：`update_conversation(..., expected_revision=...)`。

- [x] 先验证 Memory/SQL 新建对话返回 `revision == 1`。
- [x] 先验证服务端保留区 patch 在 revision 匹配时写入并递增，在旧 revision 下失败且不改变状态。
- [x] 先验证普通 context 全量替换保留当前 `__agent_runtime` 和剪映字段，并拒绝客户端注入新的保留区。
- [x] 先验证两个 SQL Store 使用同一个 `expected_revision` 并发写时恰好一个成功、一个冲突。
- [x] 先验证路由响应携带 revision，`PUT` 可选 CAS 冲突映射为 HTTP 409，并且创建/更新请求不能写入保留区。
- [x] 运行新测试，确认因 revision、冲突异常和专用方法尚不存在而失败。

## 任务二：实现 Store 聚合 revision 与双实现 CAS

**文件：**

- 修改：`backend/pixelflow/tasks/store.py`
- 修改：`backend/pixelflow/tasks/__init__.py`

**接口：**

```python
class ConversationRevisionConflictError(RuntimeError):
    expected_revision: int
    current_revision: int


async def patch_agent_runtime_conversation_context(
    conversation_id: str,
    *,
    user_id: str | None,
    expected_revision: int,
    runtime_patch: dict[str, Any],
) -> PixelFlowConversationRecord | None: ...
```

- [x] 给对话记录和序列化增加 `revision`，并集中校验 revision 为正整数。
- [x] 将普通 context 替换改为保留 `__agent_runtime` 与现有剪映字段；当前不存在的保留区不能由 replacement 新增。
- [x] Memory Store 在 conversation 锁内比较 revision、合并专用 runtime patch、递增 revision；冲突不得修改对象。
- [x] SQL Store 在数据库事务和 `SELECT ... FOR UPDATE` 内比较 revision、完成更新并递增；SQLite 继续使用 `BEGIN IMMEDIATE` 覆盖多 Store 并发。
- [x] 剪映原子 patch 在实际状态发生变化时递增 revision，并保持现有 job 单调性。
- [x] 运行新合同，确认 Memory/SQL Store 组变绿。

## 任务三：补齐 ORM、迁移、MySQL 旧库升级与 API

**文件：**

- 修改：`backend/pixelflow/tasks/model.py`
- 修改：`backend/pixelflow/tasks/mysql.py`
- 新增：`backend/packages/harness/deerflow/persistence/migrations/versions/20260724_02_conversation_revision.py`
- 修改：`backend/app/gateway/routers/pixelflow_conversations.py`

- [x] ORM 新增非空整数 `revision`，数据库默认值为 `1`。
- [x] Alembic `20260724_02` 对已存在的 `pixelflow_conversations` 增加并回填 revision；目标旧表不存在时不得破坏 M01.1 的纯新 Runtime migration。
- [x] PixelFlow 独立 MySQL 初始化在旧表缺列时执行一次兼容升级，避免 `create_all()` 无法修改既有表。
- [x] API 创建时丢弃客户端 `__agent_runtime`；响应增加 revision；更新请求支持可选 `expected_revision`，冲突返回 409。
- [x] 保持所有旧路径、字段和无 revision 的前端请求兼容。
- [x] 运行迁移、路由和剪映专项回归。

## 任务四：验证、审核与交接

**文件：**

- 修改：`docs/agentization/status/M01-status.md`
- 新增：`docs/agentization/test-reports/M01.3.md`

- [x] 运行 M01.3 新合同、旧 Store、对话路由、owner isolation、M01.1 migration、M01.2 Repository 和剪映原子 patch 回归。
- [x] 对全部变更 Python 路径运行 ruff，并运行 `git diff --check`。
- [x] 启动独立只读 reviewer，重点检查多进程 SQL CAS、跨用户隔离、保留区写入边界、旧 API 兼容和是否越界实现 M01.4/M01.5。
- [x] 处理 Critical/Important 意见并重新运行切片门禁。
- [x] 用中文更新状态与测试报告，释放写入权，把下一切片设为 M01.4；phase 保持 `in_progress`，不得写 ready 状态。
- [x] 通过中文工程规范检查后创建一个中文提交，推送模块分支并核对远端 SHA，随后停止。
