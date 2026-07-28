# M06 持久化 External Job Coordinator

- phase：`in_progress`
- owner：A
- branch：`codex/agent-0.8.4-m06-external-jobs`
- 依赖：M01、M02
- 当前切片：`M06.4`
- 最近完成：`M06.4`
- base Agent SHA：`340a7e42a5d1c918c3c662e29ce833da41665f82`
- M06.1 开始时间：`2026-07-28T14:53:54+08:00`
- M06.1 完成时间：`2026-07-28T15:07:21+08:00`
- M06.2 开始时间：`2026-07-28T15:44:47+08:00`
- M06.2 完成时间：`2026-07-28T16:00:31+08:00`
- M06.3 开始时间：`2026-07-28T16:22:27+08:00`
- M06.3 完成时间：`2026-07-28T17:03:47+08:00`
- M06.4 开始时间：`2026-07-28T17:18:06+08:00`
- M06.4 完成时间：`2026-07-28T17:52:18+08:00`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- worktree：`E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow-worktrees\m06-external-jobs`

## M06.4 锁定范围

- `backend/pixelflow/agent_runtime/jobs/**`
- `backend/pixelflow/agent_runtime/persistence/repositories.py`
- `backend/tests/test_agent_runtime_operation_completion.py`
- `README.md`
- `AGENTS.md`
- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `docs/agentization/plans/2026-07-28-m06-4-operation-completion-resume.md`
- `docs/agentization/test-reports/M06.4.md`
- `docs/agentization/status/M06-status.md`

## M06.3 锁定范围

- `backend/pixelflow/agent_runtime/jobs/**`
- `backend/pixelflow/agent_runtime/ports.py`
- `backend/pixelflow/agent_runtime/fakes.py`
- `backend/tests/test_agent_runtime_provider_job_adapter.py`
- `README.md`
- `AGENTS.md`
- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `docs/agentization/plans/2026-07-28-m06-3-provider-job-adapter.md`
- `docs/agentization/test-reports/M06.3.md`
- `docs/agentization/status/M06-status.md`

## M06.2 锁定范围

- `backend/pixelflow/agent_runtime/jobs/**`
- `backend/pixelflow/agent_runtime/persistence/repositories.py`
- `backend/tests/test_agent_runtime_operation_leases.py`
- `README.md`
- `AGENTS.md`
- `docs/pixelflow-agent-skill-flow-latest-design.md`
- `docs/agentization/plans/2026-07-28-m06-2-operation-leases.md`
- `docs/agentization/test-reports/M06.2.md`
- `docs/agentization/status/M06-status.md`

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
- [x] M06.2 DB lease/heartbeat/接管（3h）
- [x] M06.3 provider job adapter（2.5h）
- [x] M06.4 graph resume/终态 claim/crash window（2.5h）
- [ ] M06.5 shutdown/restart/expired 恢复（2h）

## M06.4 交付记录

- 产物：新增 `OperationCompletionCoordinator`、`OperationCompletionDispatcher` 和 `WorkflowGraphResumePort`，Memory/SQL Repository 在同一临界区或事务内原子保存 Operation 终态与 `external_job.state_changed` 完成事件；复用现有表结构，不新增 migration。
- 幂等与恢复：完成事件 ID、cursor 和 run ID 只从内部 job ID 稳定派生；Dispatcher 按事件 ID 领取定向投递租约，并把 `event_id` 作为 Graph checkpoint 幂等键。Provider 完成后、Graph checkpoint 前后发生进程退出都只重放同一持久化结果，不再次调用供应商 start。
- 顺序与租约：通用 Outbox claim 先检查最小未发布 sequence，队首是 Operation 完成事件时返回空，不能越过它领取后续普通事件；Graph 返回后按实际完成时间确认租约，过期 worker 不能确认投递。
- 只读合同：Operation 完成快照、事件 envelope、payload、嵌套业务结果和 Graph 入参均深度只读，同时 `model_dump(mode="json")` 与 `model_dump_json()` 仍输出普通 JSON。
- TDD：首轮因 M06.4 类型缺失产生明确 ImportError；自审和独立审核追加通用 claim 抢占、完成后时钟、队首 sequence、深度只读和冻结容器序列化多轮 RED。最终 completion 为 `41 passed, 1 warning`，核心相关回归 `198 passed, 1 warning`，M00/M01/M02/M06 合并定向 `340 passed, 1 warning`，全部 Agent Runtime 扩展回归 `715 passed, 1 warning`。
- 静态检查：变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：`/root/m06_4_reviewer` 全程只读；三个有效 Important 均先补失败测试并修复，最终 Critical / Important / Minor 均无，`Ready to commit/push：是`。reviewer 独立复跑 completion `41 passed`、核心相关 `198 passed`、全部 Agent Runtime `715 passed`。
- 隔离与成本：未新增或修改配置、数据库表/字段/索引/migration、HTTP API 或 content-app 合同，未调用真实图片、视频、PPT、视频分析、剪映、LLM 或其他付费服务，未修改两个长期 feature 分支，也未实现 M06.5 的扫描、shutdown/restart、404/expired 或人工恢复。
- 文档：已同步 `README.md`、`AGENTS.md`、最新设计、实施计划、本状态和 `docs/agentization/test-reports/M06.4.md`；明确 M06.1–M06.4 仍未进入 Agent 长期分支。
- 阶段状态：M06.4 不是 `phased-rollout-plan.md` 明确检查点或模块最终切片，保持 `in_progress`，不更新 `status/BOARD.md`，不写任何 ready 状态，也不触发 9.10A。
- commit/push：本状态随 M06.4 中文独立提交推送到 `origin/codex/agent-0.8.4-m06-external-jobs`，远端以该提交为准。
- 下一切片：M06.5 shutdown/restart recovery、job 404/expired 与人工恢复语义；必须由开发者后续明确启动并重新领取唯一 writer，继续使用同一模块分支/worktree。

## M06.3 交付记录

- 产物：新增 `ProviderJobAdapter`、`ExistingJobService` Protocol 和深度只读 `ProviderJobSnapshot`，把现有 v2 Mapping/Pydantic start/status DTO 归一为 `polling/succeeded/failed/paused_quota/timeout` 五态；不修改现有 Router。
- 兼容边界：支持真实 `quota_paused` 别名、直接异常属性或 httpx response 的 HTTP 402、结构化额度标记、内置/httpx 超时；现有 DTO 中明确的 `raw/raw_response/provider_response/response_body` 字段先递归剔除，剩余业务 JSON 才进入 Snapshot。
- 安全边界：provider job ID 固定受限字符和 255 字符上限，拒绝 URL、空白和疑似凭据形态；敏感键、普通字符串中的 Authorization/Bearer/token、带 userinfo/query/fragment 的 HTTP(S) URL、非法 JSON、非有限浮点和未知状态均 fail-closed。Snapshot 固定 outcome/reason/message 对应关系，顶层和嵌套结果不可变，序列化前再次校验；错误输入和供应商异常原文不回显。
- TDD：首轮因 Adapter 不存在产生明确 ImportError；自审和独立审核持续追加多轮 RED，覆盖非布尔 `ok`、真实 DTO raw、状态别名、job ID、凭据值、公开模型绕过、赋值残留与嵌套篡改。最终定向 GREEN 为 `66 passed`，相关合同回归 `237 passed`，全部 Agent Runtime 扩展回归 `674 passed`。唯一 warning 为既有 LangChain pending deprecation。
- 静态检查：变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：`/root/m06_3_reviewer` 全程只读，独立复跑 Provider Adapter、operation 与 lease 为 `144 passed`；最终结论为无 Critical / Important / Minor，`Ready to commit/push：是`。
- 隔离与成本：未新增或修改配置、数据库表/字段/索引/migration、HTTP API 或 content-app 合同，未调用真实图片、视频、PPT、视频分析、剪映、LLM 或其他付费服务，未修改两个长期 feature 分支。
- 文档：已同步 `README.md`、`AGENTS.md`、最新设计、实施计划、本状态和 `docs/agentization/test-reports/M06.3.md`；明确 M06.1–M06.3 仍未进入 Agent 长期分支。
- 阶段状态：M06.3 不是 `phased-rollout-plan.md` 明确检查点或模块最终切片，保持 `in_progress`，不更新 `status/BOARD.md`，不写任何 ready 状态，也不自动继续 M06.4。
- commit/push：本状态随 M06.3 中文独立提交推送到 `origin/codex/agent-0.8.4-m06-external-jobs`，远端以该提交为准。
- 下一切片：M06.4 完成事件、Workflow Graph resume、终态 claim 和“Provider 成功/checkpoint 前崩溃”窗口；必须由开发者后续明确启动并重新领取唯一 writer，继续使用同一模块分支/worktree。

## M06.2 交付记录

- 产物：新增 `OperationLeaseCoordinator`，Memory/SQL Repository 增加到期领取、heartbeat 和下次轮询排期三个原子方法；复用 M01 已落库字段，不新增表、字段、索引或 migration。
- 竞争边界：只有 `polling + provider_job_id + next_poll_at <= now` 的 operation 可领取；SQL 在事务中锁行，SQLite 使用 `BEGIN IMMEDIATE`，两个独立 Engine/worker 同时竞争只保留一个胜出者。
- 租约边界：有效期内同 worker 重领只回读且不隐式续期，heartbeat 必须严格延长；持有者原子写入未来 `next_poll_at` 并释放 lease，过期边界允许新 worker 接管，旧 worker 随即失去 heartbeat 和排期权限。
- 隔离与安全：所有租约写入同时匹配用户、对话和内部 job；不匹配统一返回不可领取。未保存供应商原始请求、Authorization、token 或密钥，未修改配置、HTTP API、content-app 合同或两个长期 feature 分支。
- TDD：初始因 `OperationLeaseCoordinator` 不存在而 RED；定向 GREEN 为 `32 passed`，相关合同回归 `171 passed`，全部 Agent Runtime 扩展回归 `608 passed`。唯一 warning 为既有 LangChain pending deprecation。
- 静态检查：变更 Python 路径的 `ruff check`、`ruff format --check` 和 `git diff --check` 均通过。
- 独立审核：`/root/m06_2_reviewer` 全程只读，最终结论为无 Critical / Important / Minor；独立复跑 operation、lease、Repository pytest 为 `148 passed`。
- 文档：已同步 `README.md`、`AGENTS.md`、最新设计、实施计划、本状态和 `docs/agentization/test-reports/M06.2.md`；明确 M06.1–M06.2 仍未进入 Agent 长期分支。
- 阶段状态：M06.2 不是阶段检查点或模块最终切片，保持 `in_progress`，不更新 `status/BOARD.md`，不写任何 ready 状态，也不自动继续 M06.3。
- commit/push：本状态随 M06.2 中文独立提交推送到 `origin/codex/agent-0.8.4-m06-external-jobs`，远端以该提交为准。
- 下一切片：M06.3 Provider Job Adapter；必须由开发者后续明确启动并重新领取唯一 writer，继续使用同一模块分支/worktree。

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
