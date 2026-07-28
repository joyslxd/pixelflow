# M06 持久化 External Job Coordinator

- phase：`in_progress`
- owner：A
- branch：`codex/agent-0.8.4-m06-external-jobs`
- 依赖：M01、M02
- 当前切片：`M06.1`
- 最近完成：`M06.1`
- base Agent SHA：`340a7e42a5d1c918c3c662e29ce833da41665f82`
- M06.1 开始时间：`2026-07-28T14:53:54+08:00`
- M06.1 完成时间：`2026-07-28T15:07:21+08:00`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m06-external-jobs`

## M06.1 锁定范围

- `backend/pixelflow/agent_runtime/jobs/**`
- `backend/pixelflow/agent_runtime/persistence/repositories.py`
- `backend/tests/test_agent_runtime_operation_coordinator.py`
- `README.md`
- `AGENTS.md`
- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `docs/agentization/plans/2026-07-28-m06-1-operation-idempotency-state-machine.md`
- `docs/agentization/test-reports/M06.1.md`
- `docs/agentization/status/M06-status.md`

## 启动检查

- `origin/feature/dev_0.8.4_boguan` 已是 `origin/feature/agent_0.8.4_boguan` 的祖先。
- `Sync-DevToAgent.ps1 -Apply` 返回 `up_to_date`，未修改两个长期 feature 分支。
- Agent Runtime 合同、Repository 与 migration 基线：`93 passed`。

## 切片

- [x] M06.1 operation 幂等与状态机（2.5h）
- [ ] M06.2 DB lease/heartbeat/接管（3h）
- [ ] M06.3 provider job adapter（2.5h）
- [ ] M06.4 graph resume/终态 claim/crash window（2.5h）
- [ ] M06.5 shutdown/restart/expired 恢复（2h）

## M06.1 交付记录

- 产物：新增 `agent_runtime.jobs` 领域层，提供四段 operation 身份、规范请求 SHA-256、显式状态迁移表和持久化首次 claim；Memory/SQL Repository 新增按 owner 查询幂等键。
- 幂等边界：相同 start 的顺序或并发重试返回同一内部 job；workflow、conversation、stage、stage version、attempt 或请求摘要不一致时 fail-closed，其他 owner 不可见也不可复用。
- 安全边界：Coordinator 只持久化请求摘要，不保存供应商原始请求、Authorization、token 或密钥；未修改配置、HTTP API、数据库表、migration 或 content-app 合同。
- TDD：初始因 `agent_runtime.jobs` 不存在而 RED；定向 GREEN 为 `45 passed`，相关合同回归 `139 passed`，全部 Agent Runtime 扩展回归 `576 passed`。唯一 warning 为既有 LangChain pending deprecation。
- 静态检查：变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：`/root/m06_1_reviewer` 全程只读，最终结论为无 Critical / Important / Minor；独立复跑相关 pytest 为 `139 passed`。
- 文档：已同步 `README.md`、`AGENTS.md`、最新设计、实施计划、本状态和 `docs/agentization/test-reports/M06.1.md`；明确 M06.1 尚未进入 Agent 长期分支。
- 阶段状态：M06.1 不是阶段检查点或模块最终切片，保持 `in_progress`，不更新 `status/BOARD.md`，不写任何 ready 状态，也不自动继续 M06.2。
- commit/push：本状态随 M06.1 中文独立提交推送到 `origin/codex/agent-0.8.4-m06-external-jobs`，远端以该提交为准。
- 下一切片：M06.2 数据库 lease、heartbeat、`next_poll_at` 与过期接管；必须由开发者后续明确启动并重新领取唯一 writer，继续使用同一模块分支/worktree。

## 恢复提示

不能只依赖 checkpoint 保证不重复计费；必须覆盖“供应商已成功、checkpoint 尚未写入时进程崩溃”的窗口。
