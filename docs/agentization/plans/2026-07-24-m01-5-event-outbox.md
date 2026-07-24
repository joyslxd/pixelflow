# M01.5 Event Outbox 实施计划

## 目标

在不改动冻结 `AgentEvent` 线合同和 M01.1 表结构的前提下，为 Memory/SQL Repository 补齐 conversation 内单调 sequence、cursor 增量查询、带租约的 Event claim 与投递完成确认。该切片只提供持久化层能力，不实现 M02 Runtime、SSE Router 或前端 gap 恢复。

## 设计取舍

- 不采用“claim 即 published”：这种做法在实际发送前崩溃会永久丢失事件，不符合 Outbox 的先落库、后投递语义。
- 不新增 Event 状态 DTO 或数据库迁移：M01.1 已预留 `delivery_status`、`delivery_attempts`、`lease_owner`、`lease_expires_at` 和 `published_at`，本切片直接激活这些字段。
- 采用“租约 claim + 显式完成”两阶段：最早未发布事件被租约保护；租约有效时阻止重复领取和后续越序领取，过期后允许接管；成功发送后显式标记 published。
- cursor 继续保持不透明字符串。查询时先在当前 owner/conversation 下解析锚点；未知或跨 owner cursor 返回不存在语义，已知 cursor 返回 sequence 更大的事件。

## 合同

1. `create_event()` 只接受 conversation 的下一连续 sequence，首个事件必须为 `1`；跳号、倒序和唯一键冲突统一 fail-closed。
2. `list_events_after_cursor()`：
   - cursor 为 `None` 时从头读取；
   - cursor 可见时返回其后事件；
   - cursor 不存在或属于其他 owner 时返回 `None`，供上层映射为 404 风格；
   - limit 必须在 `1..1000`。
3. `claim_next_event()` 始终处理 sequence 最小的未发布事件：
   - pending 事件可领取；
   - 有效租约阻止重复领取及后续事件越序；
   - 过期租约可由其他 worker 接管，并递增尝试次数。
4. `complete_event_delivery()` 只允许当前有效租约完成；完成后重复确认幂等返回原事件，跨 owner 仍表现为不存在。
5. Memory 与 SQL 双实现保持相同行为；SQLite 使用既有 Engine 级进程内锁和 `BEGIN IMMEDIATE`，其他 SQL 方言使用事务与行锁。

## TDD 与验证

- [x] 新增 Memory/SQL 双实现合同测试，先确认方法缺失或行为不满足而失败。
- [x] 覆盖连续 sequence、跳号拒绝、cursor 首读/续读/末尾/未知、跨 owner 不可见。
- [x] 覆盖重复 claim、过期接管、严格顺序、完成幂等和错误 lease fail-closed。
- [x] 覆盖 Memory/SQL 并发 append 与 claim。
- [x] 实现最小 Repository 能力并使新合同转绿。
- [x] 运行 M01.5 定向测试、完整 M01 模块门禁、ruff、`git diff --check`、中文规范与分支策略检查。
- [x] 完成独立审核，更新测试报告与 M01 状态为 `ready_for_integration`，独立中文 commit 并 push。

## 边界

- 不修改 M02–M13 模块代码或状态。
- 不新增或调用真实付费 API。
- 不修改两个长期 feature 分支，不创建切片子分支或切片 worktree。
- 本任务不执行最终单槽集成；模块就绪后由开发者按执行手册 9.10A 手动启动。
